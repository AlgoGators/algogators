#include "trade_ngin/statistics/regression/ridge_regression.hpp"
#include "trade_ngin/statistics/regression/purged_kfold.hpp"
#include "trade_ngin/statistics/validation.hpp"
#include "trade_ngin/core/logger.hpp"
#include <cmath>
#include <algorithm>
#include <limits>
#include <numeric>

namespace trade_ngin {
namespace statistics {

RidgeRegression::RidgeRegression(RidgeRegressionConfig config)
    : config_(config) {}

RidgeResult RidgeRegression::fit_alpha(const Eigen::MatrixXd& Xc, const Eigen::VectorXd& yc, double alpha) const {
    int p = Xc.cols();
    Eigen::MatrixXd XtX = Xc.transpose() * Xc;
    XtX += alpha * Eigen::MatrixXd::Identity(p, p);

    Eigen::VectorXd Xty = Xc.transpose() * yc;
    Eigen::VectorXd beta = XtX.ldlt().solve(Xty);

    // R² on centered data
    Eigen::VectorXd residuals = yc - Xc * beta;
    double ss_res = residuals.squaredNorm();
    double ss_tot = yc.squaredNorm();

    RidgeResult res;
    res.coefficients = beta;
    res.r_squared = (ss_tot > 0) ? 1.0 - ss_res / ss_tot : 1.0;
    res.best_alpha = alpha;
    return res;
}

Result<RidgeResult> RidgeRegression::fit(const Eigen::MatrixXd& X, const Eigen::VectorXd& y) {
    std::lock_guard<std::mutex> lock(mutex_);

    {
        auto valid = validation::validate_matrix(X, 2, 1, "RidgeRegression");
        if (valid.is_error()) return make_error<RidgeResult>(valid.error()->code(), valid.error()->what(), "RidgeRegression");
    }

    if (X.rows() != y.size()) {
        return make_error<RidgeResult>(ErrorCode::INVALID_ARGUMENT,
            "X rows must match y size", "RidgeRegression");
    }

    if (!y.allFinite()) {
        return make_error<RidgeResult>(ErrorCode::INVALID_DATA, "NaN/Inf detected in y", "RidgeRegression");
    }

    DEBUG("[RidgeRegression::fit] n=" << X.rows() << " p=" << X.cols() << " alpha=" << config_.alpha);

    double best_alpha = config_.alpha;
    std::vector<double> cv_mse;

    // Cross-validation on the raw rows: each fold centres on its own training rows and
    // predicts with its own intercept (purged_cv_mse), so no test row reaches a fold's fit.
    if (config_.cv_folds > 1 && !config_.alpha_candidates.empty()) {
        auto folds = purged_kfold(static_cast<int>(X.rows()), config_.cv_folds,
                                  config_.cv_purge, config_.cv_embargo);
        if (folds.is_error()) {
            return make_error<RidgeResult>(folds.error()->code(), folds.error()->what(), "RidgeRegression");
        }

        double best_mse = std::numeric_limits<double>::infinity();
        for (double a : config_.alpha_candidates) {
            double mse = purged_cv_mse(X, y, folds.value(), config_.include_intercept,
                [this, a](const Eigen::MatrixXd& Xtr, const Eigen::VectorXd& ytr) {
                    return fit_alpha(Xtr, ytr, a).coefficients;
                });
            cv_mse.push_back(mse);
            if (mse < best_mse) {
                best_mse = mse;
                best_alpha = a;
            }
        }
        DEBUG("[RidgeRegression::fit] CV selected alpha=" << best_alpha);
    }

    // Centre X and y for the intercept (not penalised); without one the fit goes through the origin.
    Eigen::MatrixXd Xc;
    Eigen::VectorXd yc;
    if (config_.include_intercept) {
        x_mean_ = X.colwise().mean();
        y_mean_ = y.mean();
        Xc = X.rowwise() - x_mean_.transpose();
        yc = y.array() - y_mean_;
    } else {
        x_mean_ = Eigen::VectorXd::Zero(X.cols());
        y_mean_ = 0.0;
        Xc = X;
        yc = y;
    }

    result_ = fit_alpha(Xc, yc, best_alpha);

    // Compute intercept
    if (config_.include_intercept) {
        result_.intercept = y_mean_ - x_mean_.dot(result_.coefficients);
    }

    result_.best_alpha = best_alpha;
    result_.cv_mse = std::move(cv_mse);
    fitted_ = true;

    return Result<RidgeResult>(result_);
}

Result<Eigen::VectorXd> RidgeRegression::predict(const Eigen::MatrixXd& X) const {
    std::lock_guard<std::mutex> lock(mutex_);

    if (!fitted_) {
        return make_error<Eigen::VectorXd>(ErrorCode::NOT_INITIALIZED,
            "Ridge model has not been fitted", "RidgeRegression");
    }

    if (X.cols() != result_.coefficients.size()) {
        return make_error<Eigen::VectorXd>(ErrorCode::INVALID_ARGUMENT,
            "Feature dimension mismatch", "RidgeRegression");
    }

    Eigen::VectorXd predictions = X * result_.coefficients;
    if (config_.include_intercept) {
        predictions.array() += result_.intercept;
    }

    return Result<Eigen::VectorXd>(std::move(predictions));
}

} // namespace statistics
} // namespace trade_ngin

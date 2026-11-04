#pragma once

#include "trade_ngin/core/error.hpp"
#include <Eigen/Dense>
#include <utility>
#include <vector>

namespace trade_ngin {
namespace statistics {

/**
 * @brief One cross-validation split: row indices into the caller's X and y, both ascending.
 */
struct CVFold {
    std::vector<int> train_indices;
    std::vector<int> test_indices;
};

/**
 * @brief Contiguous k-fold splits for time-ordered rows, with a purge and an embargo.
 *
 * Fold f tests the block [f*m, (f+1)*m) with m = n / k; the last fold also takes the remainder.
 * Every row outside the block trains, except:
 * - purge: `purge` rows on each side of the block, [start - purge, start) and [end, end + purge).
 *   Set it to the label horizon in rows: a training row whose label window reaches into the test
 *   block's (or starts inside it) would otherwise leak the test labels into the fit.
 * - embargo: a further `embargo` rows after the purged rows, [end + purge, end + purge + embargo).
 *
 * With purge = 0 and embargo = 0 the splits are the plain contiguous k-fold the lasso and ridge
 * used before the purge existed.
 *
 * Errors (INVALID_ARGUMENT): k < 2, k > n, purge < 0, embargo < 0, or a fold left with fewer than
 * min_train training rows.
 */
Result<std::vector<CVFold>> purged_kfold(int n, int k, int purge, int embargo, int min_train = 2);

/**
 * @brief Mean over folds of the held-out mean squared error of one linear fit.
 *
 * All preprocessing is fitted on each fold's training rows only. With include_intercept the
 * training rows are centred on their own means, `fit_coefficients(X_train_c, y_train_c)` returns
 * the slopes, and the test rows are predicted as X_test * beta + (mean(y_train) - mean(X_train) . beta).
 * Without it nothing is centred and the test rows are predicted as X_test * beta.
 *
 * @param fit_coefficients callable (const Eigen::MatrixXd&, const Eigen::VectorXd&) -> Eigen::VectorXd
 */
template <typename FitCoefficients>
double purged_cv_mse(const Eigen::MatrixXd& X, const Eigen::VectorXd& y,
                     const std::vector<CVFold>& folds, bool include_intercept,
                     FitCoefficients&& fit_coefficients) {
    const Eigen::Index p = X.cols();
    double total_mse = 0.0;

    for (const auto& fold : folds) {
        const Eigen::Index n_train = static_cast<Eigen::Index>(fold.train_indices.size());
        const Eigen::Index n_test = static_cast<Eigen::Index>(fold.test_indices.size());

        Eigen::MatrixXd X_train(n_train, p);
        Eigen::VectorXd y_train(n_train);
        for (Eigen::Index r = 0; r < n_train; ++r) {
            X_train.row(r) = X.row(fold.train_indices[static_cast<size_t>(r)]);
            y_train(r) = y(fold.train_indices[static_cast<size_t>(r)]);
        }

        Eigen::RowVectorXd x_mean = Eigen::RowVectorXd::Zero(p);
        double y_mean = 0.0;
        if (include_intercept) {
            x_mean = X_train.colwise().mean();
            y_mean = y_train.mean();
            X_train.rowwise() -= x_mean;
            y_train.array() -= y_mean;
        }

        const Eigen::VectorXd beta = fit_coefficients(X_train, y_train);
        const double intercept = include_intercept ? y_mean - x_mean.dot(beta) : 0.0;

        double sse = 0.0;
        for (Eigen::Index r = 0; r < n_test; ++r) {
            const int i = fold.test_indices[static_cast<size_t>(r)];
            const double err = y(i) - (X.row(i).dot(beta) + intercept);
            sse += err * err;
        }
        total_mse += sse / static_cast<double>(n_test);
    }

    return total_mse / static_cast<double>(folds.size());
}

} // namespace statistics
} // namespace trade_ngin

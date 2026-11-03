// Cross-validation and intercept behaviour shared by LassoRegression and RidgeRegression
// (AlgoGators/algogators#189): purged folds, centring inside each fold, and the intercept
// offset at prediction.

#include <gtest/gtest.h>
#include "trade_ngin/statistics/regression/lasso_regression.hpp"
#include "trade_ngin/statistics/regression/purged_kfold.hpp"
#include "trade_ngin/statistics/regression/ridge_regression.hpp"
#include <Eigen/Dense>
#include <algorithm>
#include <random>
#include <set>
#include <vector>

using namespace trade_ngin::statistics;

namespace {

// y = 2x + 1 on x = 1..4. Through the origin the least-squares slope is
// sum(xy) / sum(x^2) = 70 / 30; with an intercept it is 2.
void origin_data(Eigen::MatrixXd& X, Eigen::VectorXd& y) {
    X.resize(4, 1);
    y.resize(4);
    X << 1.0, 2.0, 3.0, 4.0;
    y << 3.0, 5.0, 7.0, 9.0;
}

// A feature that is 0 on the first half and 1 on the second, with y = 10 * feature.
// Under 2-fold CV each training fold is one half, where the feature is constant: no fold
// can learn anything from it, so every alpha must score the same.
void block_data(Eigen::MatrixXd& X, Eigen::VectorXd& y) {
    const int n = 20;
    X.resize(n, 1);
    y.resize(n);
    for (int i = 0; i < n; ++i) {
        X(i, 0) = (i < n / 2) ? 0.0 : 1.0;
        y(i) = 10.0 * X(i, 0);
    }
}

}  // namespace

// ---------------------------------------------------------------------------
// Fix 3: without an intercept the model is fitted and predicted through the origin.
// The parent centred X and y anyway, fitted slope 2, then predicted 2x with no offset.
// ---------------------------------------------------------------------------

TEST(RegressionInterceptTest, RidgeWithoutInterceptFitsThroughOrigin) {
    Eigen::MatrixXd X;
    Eigen::VectorXd y;
    origin_data(X, y);

    RidgeRegressionConfig config;
    config.include_intercept = false;
    config.alpha = 0.0;
    RidgeRegression ridge(config);
    auto fit = ridge.fit(X, y);
    ASSERT_TRUE(fit.is_ok());

    EXPECT_NEAR(fit.value().coefficients(0), 70.0 / 30.0, 1e-12);
    EXPECT_EQ(fit.value().intercept, 0.0);

    auto pred = ridge.predict(X);
    ASSERT_TRUE(pred.is_ok());
    // Normal equation of the no-intercept fit: residuals orthogonal to X.
    EXPECT_NEAR(X.col(0).dot(y - pred.value()), 0.0, 1e-10);
}

TEST(RegressionInterceptTest, LassoWithoutInterceptFitsThroughOrigin) {
    Eigen::MatrixXd X;
    Eigen::VectorXd y;
    origin_data(X, y);

    LassoRegressionConfig config;
    config.include_intercept = false;
    config.alpha = 0.0;
    LassoRegression lasso(config);
    auto fit = lasso.fit(X, y);
    ASSERT_TRUE(fit.is_ok());

    EXPECT_NEAR(fit.value().coefficients(0), 70.0 / 30.0, 1e-12);
    EXPECT_EQ(fit.value().intercept, 0.0);

    auto pred = lasso.predict(X);
    ASSERT_TRUE(pred.is_ok());
    EXPECT_NEAR(X.col(0).dot(y - pred.value()), 0.0, 1e-10);
}

TEST(RegressionInterceptTest, WithInterceptPredictionsAddTheOffset) {
    Eigen::MatrixXd X;
    Eigen::VectorXd y;
    origin_data(X, y);

    RidgeRegressionConfig rc;
    rc.alpha = 0.0;
    RidgeRegression ridge(rc);
    ASSERT_TRUE(ridge.fit(X, y).is_ok());
    EXPECT_NEAR(ridge.result().intercept, 1.0, 1e-12);
    EXPECT_NEAR((ridge.predict(X).value() - y).cwiseAbs().maxCoeff(), 0.0, 1e-12);

    LassoRegressionConfig lc;
    lc.alpha = 0.0;
    LassoRegression lasso(lc);
    ASSERT_TRUE(lasso.fit(X, y).is_ok());
    EXPECT_NEAR(lasso.result().intercept, 1.0, 1e-12);
    EXPECT_NEAR((lasso.predict(X).value() - y).cwiseAbs().maxCoeff(), 0.0, 1e-12);
}

// ---------------------------------------------------------------------------
// Fix 2: centring inside each fold. On block_data a fold's training rows hold the feature
// constant, so no alpha can do better than another and the first candidate stands. The parent
// centred on the full sample, which gave each training half a non-zero feature carrying the
// other half's mean, so the near-OLS alpha "predicted" the test half exactly and won.
// ---------------------------------------------------------------------------

TEST(RegressionFoldCentringTest, RidgeCannotLearnFromAFeatureConstantInEveryTrainingFold) {
    Eigen::MatrixXd X;
    Eigen::VectorXd y;
    block_data(X, y);

    RidgeRegressionConfig config;
    config.cv_folds = 2;
    config.alpha_candidates = {1e6, 1e-8};
    RidgeRegression ridge(config);
    auto fit = ridge.fit(X, y);
    ASSERT_TRUE(fit.is_ok());
    EXPECT_EQ(fit.value().best_alpha, 1e6);
}

TEST(RegressionFoldCentringTest, LassoCannotLearnFromAFeatureConstantInEveryTrainingFold) {
    Eigen::MatrixXd X;
    Eigen::VectorXd y;
    block_data(X, y);

    LassoRegressionConfig config;
    config.cv_folds = 2;
    config.alpha_candidates = {1e6, 1e-8};
    LassoRegression lasso(config);
    auto fit = lasso.fit(X, y);
    ASSERT_TRUE(fit.is_ok());
    EXPECT_EQ(fit.value().best_alpha, 1e6);
}

namespace {

void noisy_data(Eigen::MatrixXd& X, Eigen::VectorXd& y, int n, int p, unsigned seed) {
    std::mt19937 gen(seed);
    std::normal_distribution<> z(0.0, 1.0);
    X.resize(n, p);
    y.resize(n);
    for (int i = 0; i < n; ++i) {
        double yi = 0.5;
        for (int j = 0; j < p; ++j) {
            X(i, j) = 2.0 + z(gen);  // non-zero means, so centring matters
            yi += (j + 1) * 0.7 * X(i, j);
        }
        y(i) = yi + z(gen);
    }
}

// Uniform data from raw mt19937 output, which the standard fixes on every platform
// (std::normal_distribution is implementation-defined, so it cannot feed a golden test).
void portable_data(Eigen::MatrixXd& X, Eigen::VectorXd& y, int n, int p, unsigned seed) {
    std::mt19937 gen(seed);
    auto unit = [&gen]() { return static_cast<double>(gen()) / 4294967296.0; };
    X.resize(n, p);
    y.resize(n);
    for (int i = 0; i < n; ++i) {
        double yi = 0.5;
        for (int j = 0; j < p; ++j) {
            X(i, j) = 2.0 + 3.0 * (unit() - 0.5);
            yi += (j + 1) * 0.7 * X(i, j);
        }
        y(i) = yi + 2.0 * (unit() - 0.5);
    }
}

Eigen::MatrixXd take_rows(const Eigen::MatrixXd& X, const std::vector<int>& idx) {
    Eigen::MatrixXd out(static_cast<Eigen::Index>(idx.size()), X.cols());
    for (size_t r = 0; r < idx.size(); ++r) out.row(static_cast<Eigen::Index>(r)) = X.row(idx[r]);
    return out;
}

Eigen::VectorXd take_rows(const Eigen::VectorXd& y, const std::vector<int>& idx) {
    Eigen::VectorXd out(static_cast<Eigen::Index>(idx.size()));
    for (size_t r = 0; r < idx.size(); ++r) out(static_cast<Eigen::Index>(r)) = y(idx[r]);
    return out;
}

// The folds written out independently of purged_kfold: test block [start, end), training rows
// outside [start - purge, end + purge + embargo).
std::vector<std::pair<std::vector<int>, std::vector<int>>> expected_folds(int n, int k, int purge,
                                                                          int embargo) {
    std::vector<std::pair<std::vector<int>, std::vector<int>>> out;
    const int m = n / k;
    for (int f = 0; f < k; ++f) {
        const int start = f * m;
        const int end = (f == k - 1) ? n : start + m;
        std::vector<int> train, test;
        for (int i = 0; i < n; ++i) {
            if (i >= start && i < end) test.push_back(i);
            else if (i < start - purge || i >= end + purge + embargo) train.push_back(i);
        }
        out.emplace_back(train, test);
    }
    return out;
}

// Held-out MSE averaged over folds, each fold fitted by a fresh model (no CV) on its training
// rows only and scored through predict(): the model's own centring and intercept, nothing
// computed on test rows.
template <typename Model, typename Config>
double reference_cv_mse(const Eigen::MatrixXd& X, const Eigen::VectorXd& y, Config config,
                        int k, int purge, int embargo) {
    config.cv_folds = 0;
    config.alpha_candidates.clear();
    double total = 0.0;
    for (const auto& fold : expected_folds(static_cast<int>(X.rows()), k, purge, embargo)) {
        Model model(config);
        EXPECT_TRUE(model.fit(take_rows(X, fold.first), take_rows(y, fold.first)).is_ok());
        auto pred = model.predict(take_rows(X, fold.second));
        EXPECT_TRUE(pred.is_ok());
        total += (take_rows(y, fold.second) - pred.value()).squaredNorm() /
                 static_cast<double>(fold.second.size());
    }
    return total / static_cast<double>(k);
}

}  // namespace

// ---------------------------------------------------------------------------
// Fix 1: the purged folds.
// ---------------------------------------------------------------------------

TEST(PurgedKFoldTest, NoTrainingRowWithinThePurgeOrEmbargoOfItsTestBlock) {
    const int n = 103, k = 5, purge = 3, embargo = 2;
    auto folds = purged_kfold(n, k, purge, embargo);
    ASSERT_TRUE(folds.is_ok());
    ASSERT_EQ(folds.value().size(), static_cast<size_t>(k));

    std::set<int> tested;
    for (const auto& fold : folds.value()) {
        ASSERT_FALSE(fold.test_indices.empty());
        const int start = fold.test_indices.front();
        const int end = fold.test_indices.back() + 1;
        EXPECT_EQ(static_cast<int>(fold.test_indices.size()), end - start);
        for (int i : fold.test_indices) EXPECT_TRUE(tested.insert(i).second) << "row tested twice: " << i;

        for (int i : fold.train_indices) {
            EXPECT_FALSE(i >= start - purge && i < end + purge + embargo)
                << "training row " << i << " inside the dropped window of test block [" << start
                << ", " << end << ")";
        }
        // Every other row trains.
        const int dropped_before = std::min(purge, start);
        const int dropped_after = std::min(purge + embargo, n - end);
        EXPECT_EQ(static_cast<int>(fold.train_indices.size()),
                  n - (end - start) - dropped_before - dropped_after);
    }
    EXPECT_EQ(static_cast<int>(tested.size()), n);
    EXPECT_EQ(folds.value().back().test_indices.size(), static_cast<size_t>(n / k + n % k));
}

TEST(PurgedKFoldTest, ZeroPurgeIsThePlainContiguousKFold) {
    auto folds = purged_kfold(10, 3, 0, 0);
    ASSERT_TRUE(folds.is_ok());
    const std::vector<std::vector<int>> test = {{0, 1, 2}, {3, 4, 5}, {6, 7, 8, 9}};
    const std::vector<std::vector<int>> train = {
        {3, 4, 5, 6, 7, 8, 9}, {0, 1, 2, 6, 7, 8, 9}, {0, 1, 2, 3, 4, 5}};
    for (size_t f = 0; f < 3; ++f) {
        EXPECT_EQ(folds.value()[f].test_indices, test[f]);
        EXPECT_EQ(folds.value()[f].train_indices, train[f]);
    }
}

TEST(PurgedKFoldTest, RejectsBadArguments) {
    EXPECT_TRUE(purged_kfold(10, 1, 0, 0).is_error());   // one fold
    EXPECT_TRUE(purged_kfold(10, 11, 0, 0).is_error());  // more folds than rows
    EXPECT_TRUE(purged_kfold(10, 2, -1, 0).is_error());
    EXPECT_TRUE(purged_kfold(10, 2, 0, -1).is_error());
    // 2 folds of 5: a purge of 4 leaves fold 0 one training row.
    EXPECT_TRUE(purged_kfold(10, 2, 4, 0).is_error());
    EXPECT_TRUE(purged_kfold(10, 2, 3, 0).is_ok());
}

TEST(PurgedKFoldTest, FitReportsTheSplitterErrors) {
    Eigen::MatrixXd X;
    Eigen::VectorXd y;
    noisy_data(X, y, 10, 2, 7);

    RidgeRegressionConfig rc;
    rc.cv_folds = 2;
    rc.cv_purge = 4;
    rc.alpha_candidates = {0.1, 1.0};
    EXPECT_TRUE(RidgeRegression(rc).fit(X, y).is_error());

    LassoRegressionConfig lc;
    lc.cv_folds = 11;
    lc.alpha_candidates = {0.1, 1.0};
    EXPECT_TRUE(LassoRegression(lc).fit(X, y).is_error());
}

// ---------------------------------------------------------------------------
// The three fixes together: each candidate's reported CV score equals the score of fits made
// fold by fold on the purged training rows alone, scored through predict().
// ---------------------------------------------------------------------------

TEST(RegressionCVScoreTest, RidgeScoresEqualFoldByFoldFitsOnPurgedRows) {
    Eigen::MatrixXd X;
    Eigen::VectorXd y;
    noisy_data(X, y, 60, 3, 11);

    for (bool intercept : {true, false}) {
        RidgeRegressionConfig config;
        config.include_intercept = intercept;
        config.cv_folds = 4;
        config.cv_purge = 2;
        config.cv_embargo = 1;
        config.alpha_candidates = {0.1, 10.0, 1000.0};
        RidgeRegression ridge(config);
        auto fit = ridge.fit(X, y);
        ASSERT_TRUE(fit.is_ok());
        ASSERT_EQ(fit.value().cv_mse.size(), config.alpha_candidates.size());

        for (size_t c = 0; c < config.alpha_candidates.size(); ++c) {
            RidgeRegressionConfig one = config;
            one.alpha = config.alpha_candidates[c];
            const double expected = reference_cv_mse<RidgeRegression>(X, y, one, 4, 2, 1);
            EXPECT_NEAR(fit.value().cv_mse[c], expected, 1e-10 * expected)
                << "intercept=" << intercept << " alpha=" << one.alpha;
        }
    }
}

TEST(RegressionCVScoreTest, LassoScoresEqualFoldByFoldFitsOnPurgedRows) {
    Eigen::MatrixXd X;
    Eigen::VectorXd y;
    noisy_data(X, y, 60, 3, 13);

    for (bool intercept : {true, false}) {
        LassoRegressionConfig config;
        config.include_intercept = intercept;
        config.tolerance = 1e-12;
        config.cv_folds = 4;
        config.cv_purge = 2;
        config.cv_embargo = 1;
        config.alpha_candidates = {0.01, 0.3, 3.0};
        LassoRegression lasso(config);
        auto fit = lasso.fit(X, y);
        ASSERT_TRUE(fit.is_ok());
        ASSERT_EQ(fit.value().cv_mse.size(), config.alpha_candidates.size());

        for (size_t c = 0; c < config.alpha_candidates.size(); ++c) {
            LassoRegressionConfig one = config;
            one.alpha = config.alpha_candidates[c];
            const double expected = reference_cv_mse<LassoRegression>(X, y, one, 4, 2, 1);
            EXPECT_NEAR(fit.value().cv_mse[c], expected, 1e-10 * expected)
                << "intercept=" << intercept << " alpha=" << one.alpha;
        }
    }
}

TEST(RegressionCVScoreTest, NoCVLeavesTheScoresEmpty) {
    Eigen::MatrixXd X;
    Eigen::VectorXd y;
    noisy_data(X, y, 30, 2, 17);
    RidgeRegression ridge;
    ASSERT_TRUE(ridge.fit(X, y).is_ok());
    EXPECT_TRUE(ridge.result().cv_mse.empty());
    LassoRegression lasso;
    ASSERT_TRUE(lasso.fit(X, y).is_ok());
    EXPECT_TRUE(lasso.result().cv_mse.empty());
}

// ---------------------------------------------------------------------------
// Characterisation: with an intercept and no CV the fit is the parent's. The values were printed
// by the parent commit's binary and by this branch's, which agreed bit for bit.
// ---------------------------------------------------------------------------

TEST(RegressionCVScoreTest, NoCVFitWithInterceptIsUnchanged) {
    Eigen::MatrixXd X;
    Eigen::VectorXd y;
    portable_data(X, y, 50, 3, 21);

    RidgeRegressionConfig rc;
    rc.alpha = 0.5;
    RidgeRegression ridge(rc);
    ASSERT_TRUE(ridge.fit(X, y).is_ok());
    const auto& r = ridge.result();
    EXPECT_NEAR(r.coefficients(0), 0.66718398934576895, 1e-12);
    EXPECT_NEAR(r.coefficients(1), 1.1269980879807786, 1e-12);
    EXPECT_NEAR(r.coefficients(2), 2.0544107878154625, 1e-12);
    EXPECT_NEAR(r.intercept, 1.241532961235742, 1e-12);
    EXPECT_NEAR(r.r_squared, 0.92184873339190532, 1e-12);

    LassoRegressionConfig lc;
    lc.alpha = 0.05;
    lc.tolerance = 1e-12;
    LassoRegression lasso(lc);
    ASSERT_TRUE(lasso.fit(X, y).is_ok());
    const auto& l = lasso.result();
    EXPECT_NEAR(l.coefficients(0), 0.56889327572422954, 1e-12);
    EXPECT_NEAR(l.coefficients(1), 1.0216091040780106, 1e-12);
    EXPECT_NEAR(l.coefficients(2), 1.9994351799575567, 1e-12);
    EXPECT_NEAR(l.intercept, 1.7866496699331975, 1e-12);
    EXPECT_NEAR(l.r_squared, 0.9174551998524193, 1e-12);
}

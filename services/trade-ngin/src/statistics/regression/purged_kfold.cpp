#include "trade_ngin/statistics/regression/purged_kfold.hpp"
#include <string>

namespace trade_ngin {
namespace statistics {

Result<std::vector<CVFold>> purged_kfold(int n, int k, int purge, int embargo, int min_train) {
    if (k < 2 || k > n) {
        return make_error<std::vector<CVFold>>(ErrorCode::INVALID_ARGUMENT,
            "cv_folds must be between 2 and the number of rows (got " + std::to_string(k) +
            " for " + std::to_string(n) + " rows)", "purged_kfold");
    }
    if (purge < 0 || embargo < 0) {
        return make_error<std::vector<CVFold>>(ErrorCode::INVALID_ARGUMENT,
            "cv_purge and cv_embargo must be >= 0", "purged_kfold");
    }

    const int fold_size = n / k;
    std::vector<CVFold> folds(static_cast<size_t>(k));

    for (int f = 0; f < k; ++f) {
        const int start = f * fold_size;
        const int end = (f == k - 1) ? n : start + fold_size;
        // Rows in [drop_lo, drop_hi) do not train: the test block, the purge on both sides,
        // and the embargo after the trailing purge.
        const int drop_lo = start - purge;
        const int drop_hi = end + purge + embargo;

        CVFold& fold = folds[static_cast<size_t>(f)];
        fold.test_indices.reserve(static_cast<size_t>(end - start));
        for (int i = start; i < end; ++i) fold.test_indices.push_back(i);
        for (int i = 0; i < n; ++i) {
            if (i < drop_lo || i >= drop_hi) fold.train_indices.push_back(i);
        }

        if (static_cast<int>(fold.train_indices.size()) < min_train) {
            return make_error<std::vector<CVFold>>(ErrorCode::INVALID_ARGUMENT,
                "cv_purge " + std::to_string(purge) + " and cv_embargo " + std::to_string(embargo) +
                " leave fold " + std::to_string(f) + " with " +
                std::to_string(fold.train_indices.size()) + " training rows (need " +
                std::to_string(min_train) + ")", "purged_kfold");
        }
    }

    return Result<std::vector<CVFold>>(std::move(folds));
}

} // namespace statistics
} // namespace trade_ngin

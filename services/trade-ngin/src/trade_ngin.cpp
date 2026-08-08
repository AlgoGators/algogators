#include "trade_ngin/version.hpp"

namespace trade_ngin {

std::string_view component_name() {
    // The migrated engine will consume generated headers from platform/contracts.
    return "trade-ngin";
}

}

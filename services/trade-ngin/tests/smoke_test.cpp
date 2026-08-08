#include "trade_ngin/version.hpp"

#include <cstdlib>

int main() {
    return trade_ngin::component_name() == "trade-ngin" ? EXIT_SUCCESS : EXIT_FAILURE;
}

#include "ump/adapter.hpp"

#include <type_traits>

static_assert(std::is_polymorphic_v<ump::FrameResolver>);
static_assert(std::is_polymorphic_v<ump::ResourceObserver>);
static_assert(std::is_polymorphic_v<ump::HandoffObserver>);

int main() {
  ump::HandoffSnapshot snapshot;
  snapshot.state = ump::HandoffState::kUnknown;
  snapshot.inspection_required = true;
  return snapshot.inspection_required ? 0 : 1;
}

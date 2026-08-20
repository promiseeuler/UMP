#pragma once

#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace ump {

struct SpatialContext {
  std::string reference_frame_id;
  std::string subject_frame_id;
  std::array<double, 3> position_m{};
  std::array<double, 4> orientation_xyzw{0.0, 0.0, 0.0, 1.0};
  std::uint64_t source_time_ms{};
  std::uint64_t maximum_age_ms{};
  double position_uncertainty_m{};
  double orientation_uncertainty_rad{};
};

struct ResolvedTransform {
  SpatialContext context;
  std::uint64_t resolved_at_ms{};
};

class FrameResolver {
 public:
  virtual ~FrameResolver() = default;

  virtual std::optional<ResolvedTransform> Resolve(
      std::string_view reference_frame_id,
      std::string_view subject_frame_id,
      std::uint64_t now_ms) = 0;
};

struct ReservationLoss {
  std::string reservation_id;
  std::string task_id;
  std::vector<std::string> affected_handoff_ids;
};

class ResourceObserver {
 public:
  virtual ~ResourceObserver() = default;
  virtual void OnReservationLost(const ReservationLoss& loss) = 0;
};

enum class HandoffState {
  kProposed,
  kPrepared,
  kReady,
  kTransferring,
  kCommitted,
  kAborted,
  kFailed,
  kUnknown,
};

struct HandoffSnapshot {
  std::string handoff_id;
  std::string subject_id;
  std::string source_machine_id;
  std::string destination_machine_id;
  std::string authoritative_owner_machine_id;
  HandoffState state{HandoffState::kProposed};
  std::uint64_t revision{};
  bool retry_safe{};
  bool inspection_required{};
};

class HandoffObserver {
 public:
  virtual ~HandoffObserver() = default;
  virtual void OnHandoffChanged(const HandoffSnapshot& snapshot) = 0;
};

}  // namespace ump

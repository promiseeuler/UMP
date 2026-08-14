#include <cmath>
#include <string>
#include <unordered_set>

#include <gz/msgs/stringmsg.pb.h>
#include <gz/plugin/Register.hh>
#include <gz/sim/EntityComponentManager.hh>
#include <gz/sim/System.hh>
#include <gz/sim/Util.hh>
#include <gz/sim/components/Model.hh>
#include <gz/sim/components/Name.hh>
#include <gz/transport/Node.hh>

namespace ump::gazebo {

class ZoneMonitor final : public gz::sim::System,
                          public gz::sim::ISystemConfigure,
                          public gz::sim::ISystemPostUpdate {
 public:
  void Configure(const gz::sim::Entity &,
                 const std::shared_ptr<const sdf::Element> &sdf,
                 gz::sim::EntityComponentManager &,
                 gz::sim::EventManager &) override {
    this->centerX = sdf->Get<double>("center_x", 0.0).first;
    this->centerY = sdf->Get<double>("center_y", 0.0).first;
    this->halfExtentX = sdf->Get<double>("half_extent_x", 0.9).first;
    this->halfExtentY = sdf->Get<double>("half_extent_y", 0.9).first;
    this->stateTopic = sdf->Get<std::string>("state_topic", "/ump/zone/state").first;
    auto allowed = sdf->FindElement("allowed_model");
    while (allowed) {
      this->allowedModels.insert(allowed->Get<std::string>());
      allowed = allowed->GetNextElement("allowed_model");
    }
    this->publisher = this->node.Advertise<gz::msgs::StringMsg>(this->stateTopic);
  }

  void PostUpdate(const gz::sim::UpdateInfo &,
                  const gz::sim::EntityComponentManager &ecm) override {
    std::string intruder;
    ecm.Each<gz::sim::components::Model, gz::sim::components::Name>(
        [&](const gz::sim::Entity &entity,
            const gz::sim::components::Model *,
            const gz::sim::components::Name *name) {
          if (this->allowedModels.count(name->Data()) != 0) return true;
          const auto position = gz::sim::worldPose(entity, ecm).Pos();
          if (std::abs(position.X() - this->centerX) <= this->halfExtentX &&
              std::abs(position.Y() - this->centerY) <= this->halfExtentY) {
            intruder = name->Data();
            return false;
          }
          return true;
        });
    this->Publish(intruder.empty() ? "clear" : "intrusion:" + intruder);
  }

 private:
  void Publish(const std::string &state) {
    if (state == this->lastState) return;
    gz::msgs::StringMsg message;
    message.set_data(state);
    this->publisher.Publish(message);
    this->lastState = state;
  }

  gz::transport::Node node;
  gz::transport::Node::Publisher publisher;
  std::unordered_set<std::string> allowedModels;
  std::string stateTopic;
  std::string lastState;
  double centerX{0.0};
  double centerY{0.0};
  double halfExtentX{0.9};
  double halfExtentY{0.9};
};

}  // namespace ump::gazebo

GZ_ADD_PLUGIN(ump::gazebo::ZoneMonitor, gz::sim::System,
              gz::sim::ISystemConfigure, gz::sim::ISystemPostUpdate)

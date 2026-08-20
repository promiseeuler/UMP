#include <atomic>
#include <cmath>
#include <string>

#include <gz/msgs/boolean.pb.h>
#include <gz/msgs/stringmsg.pb.h>
#include <gz/plugin/Register.hh>
#include <gz/sim/EntityComponentManager.hh>
#include <gz/sim/Model.hh>
#include <gz/sim/System.hh>
#include <gz/sim/Util.hh>
#include <gz/sim/components/DetachableJoint.hh>
#include <gz/sim/components/Link.hh>
#include <gz/sim/components/Model.hh>
#include <gz/sim/components/Name.hh>
#include <gz/sim/components/ParentEntity.hh>
#include <gz/transport/Node.hh>

namespace ump::gazebo {

class PayloadHandoff final : public gz::sim::System,
                             public gz::sim::ISystemConfigure,
                             public gz::sim::ISystemPreUpdate {
 public:
  void Configure(const gz::sim::Entity &, const std::shared_ptr<const sdf::Element> &sdf,
                 gz::sim::EntityComponentManager &, gz::sim::EventManager &) override {
    this->payloadModel = sdf->Get<std::string>("payload_model", "package").first;
    this->payloadLink = sdf->Get<std::string>("payload_link", "body").first;
    this->armModel = sdf->Get<std::string>("arm_model", "robot_arm").first;
    this->armLink = sdf->Get<std::string>("arm_link", "tool0").first;
    this->mobileModel = sdf->Get<std::string>("mobile_model", "mobile_base").first;
    this->mobileLink = sdf->Get<std::string>("mobile_link", "base_link").first;
    this->maximumDistance = sdf->Get<double>("maximum_distance", 0.45).first;
    this->initialOwner = sdf->Get<std::string>("initial_owner", "unowned").first;
    this->stateTopic = sdf->Get<std::string>("state_topic", "/ump/payload/state").first;
    this->statePublisher = this->node.Advertise<gz::msgs::StringMsg>(this->stateTopic);
    this->node.Subscribe("/ump/payload/attach_arm", &PayloadHandoff::AttachArm, this);
    this->node.Subscribe("/ump/payload/attach_mobile", &PayloadHandoff::AttachMobile, this);
    this->node.Subscribe("/ump/payload/detach", &PayloadHandoff::Detach, this);
    this->node.Subscribe("/ump/payload/fail_next_attach", &PayloadHandoff::FailNextAttach, this);
    this->node.Subscribe("/ump/payload/drop", &PayloadHandoff::Drop, this);
  }

  void PreUpdate(const gz::sim::UpdateInfo &,
                 gz::sim::EntityComponentManager &ecm) override {
    if (!this->initialized) {
      if (this->initialOwner == "arm" && this->Attach(ecm, true, false)) {
        this->initialized = true;
        return;
      }
      if (this->initialOwner == "mobile" && this->Attach(ecm, false, false)) {
        this->initialized = true;
        return;
      }
      if (this->initialOwner == "unowned") {
        this->PublishState("unowned");
        this->initialized = true;
      }
      return;
    }
    const auto command = this->pending.exchange(Command::None);
    if (command == Command::None) return;
    if (command == Command::Detach) {
      this->RemoveJoint(ecm);
      this->PublishState("unowned");
      return;
    }
    if (command == Command::Drop) {
      this->RemoveJoint(ecm);
      this->PublishState("dropped");
      return;
    }

    const bool arm = command == Command::AttachArm;
    if (this->failNextAttach.exchange(false)) {
      this->PublishState("rejected:grasp_failed");
      return;
    }
    this->Attach(ecm, arm, true);
  }

 private:
  enum class Command { None, AttachArm, AttachMobile, Detach, Drop };

  bool Attach(gz::sim::EntityComponentManager &ecm, bool arm,
              bool enforceDistance) {
    const auto child = this->Link(ecm, this->payloadModel, this->payloadLink);
    const auto parent = this->Link(ecm, arm ? this->armModel : this->mobileModel,
                                  arm ? this->armLink : this->mobileLink);
    if (parent == gz::sim::kNullEntity || child == gz::sim::kNullEntity) {
      if (enforceDistance) this->PublishState("rejected:link_not_found");
      return false;
    }
    const auto parentPose = gz::sim::worldPose(parent, ecm);
    const auto childPose = gz::sim::worldPose(child, ecm);
    if (enforceDistance &&
        parentPose.Pos().Distance(childPose.Pos()) > this->maximumDistance) {
      this->PublishState("rejected:out_of_range");
      return false;
    }
    this->RemoveJoint(ecm);
    this->joint = ecm.CreateEntity();
    ecm.CreateComponent(
        this->joint,
        gz::sim::components::DetachableJoint(
            {parent, child, "fixed"}));
    this->PublishState(arm ? "arm" : "mobile");
    return true;
  }

  void AttachArm(const gz::msgs::Boolean &message) {
    if (message.data()) this->pending.store(Command::AttachArm);
  }
  void AttachMobile(const gz::msgs::Boolean &message) {
    if (message.data()) this->pending.store(Command::AttachMobile);
  }
  void Detach(const gz::msgs::Boolean &message) {
    if (message.data()) this->pending.store(Command::Detach);
  }
  void FailNextAttach(const gz::msgs::Boolean &message) {
    if (message.data()) this->failNextAttach.store(true);
  }
  void Drop(const gz::msgs::Boolean &message) {
    if (message.data()) this->pending.store(Command::Drop);
  }

  gz::sim::Entity Link(gz::sim::EntityComponentManager &ecm,
                       const std::string &modelName,
                       const std::string &linkName) const {
    const auto model = ecm.EntityByComponents(
        gz::sim::components::Model(), gz::sim::components::Name(modelName));
    if (model == gz::sim::kNullEntity) return gz::sim::kNullEntity;
    return ecm.EntityByComponents(gz::sim::components::Link(),
                                  gz::sim::components::Name(linkName),
                                  gz::sim::components::ParentEntity(model));
  }

  void RemoveJoint(gz::sim::EntityComponentManager &ecm) {
    if (this->joint != gz::sim::kNullEntity) {
      ecm.RequestRemoveEntity(this->joint);
      this->joint = gz::sim::kNullEntity;
    }
  }

  void PublishState(const std::string &state) {
    gz::msgs::StringMsg message;
    message.set_data(state);
    this->statePublisher.Publish(message);
  }

  gz::transport::Node node;
  gz::transport::Node::Publisher statePublisher;
  std::atomic<Command> pending{Command::None};
  std::atomic<bool> failNextAttach{false};
  gz::sim::Entity joint{gz::sim::kNullEntity};
  std::string payloadModel;
  std::string payloadLink;
  std::string armModel;
  std::string armLink;
  std::string mobileModel;
  std::string mobileLink;
  std::string stateTopic;
  std::string initialOwner;
  double maximumDistance{0.45};
  bool initialized{false};
};

}  // namespace ump::gazebo

GZ_ADD_PLUGIN(ump::gazebo::PayloadHandoff, gz::sim::System,
              gz::sim::ISystemConfigure, gz::sim::ISystemPreUpdate)

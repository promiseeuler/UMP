from pathlib import Path
import xml.etree.ElementTree as ET


ROOT = Path(__file__).parents[2] / "ump_gazebo"
REFERENCE = ROOT / "models" / "ump_mobile_base" / "model.sdf"
ALTERNATE = ROOT / "models" / "alternate" / "ump_mobile_base" / "model.sdf"


def profile(path: Path) -> tuple[str, str, str, str]:
    model = ET.parse(path).getroot().find("model")
    assert model is not None
    links = {link.get("name") for link in model.findall("link")}
    assert "base_link" in links
    plugin = model.find("plugin[@name='gz::sim::systems::DiffDrive']")
    assert plugin is not None
    assert plugin.findtext("topic") == "/model/mobile_base/cmd_vel"
    assert plugin.findtext("odom_topic") == "/model/mobile_base/odometry"
    return (
        model.findtext("link/inertial/mass", ""),
        model.findtext("link/collision/geometry/box/size", ""),
        plugin.findtext("wheel_separation", ""),
        plugin.findtext("wheel_radius", ""),
    )


def test_mobile_profiles_share_the_adapter_contract_but_are_physically_distinct():
    reference = profile(REFERENCE)
    alternate = profile(ALTERNATE)
    assert reference != alternate

    world = ET.parse(ROOT / "worlds" / "s4_warehouse.sdf").getroot()
    include = world.find("world/include")
    assert include is not None
    assert include.findtext("uri") == "model://ump_mobile_base"
    assert include.findtext("name") == "mobile_base"

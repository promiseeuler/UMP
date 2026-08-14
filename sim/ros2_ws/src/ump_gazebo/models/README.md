# S4 model resources

S4 keeps every model local so the headless scenario has no network dependency.

`ump_mobile_base` is the reference implementation of the mobile adapter
contract. `alternate/ump_mobile_base` is a compact model with different mass,
geometry, wheel separation, and wheel radius. Both expose:

- model instance `mobile_base` and root link `base_link`;
- `/model/mobile_base/cmd_vel` as differential-drive input; and
- `/model/mobile_base/odometry` as odometry output.

Set `UMP_MOBILE_MODEL_VARIANT=alternate` to put the alternate resource first.
The world, UMP identities, coordinator, mission, capabilities, and handoff
logic remain unchanged.

# Storyboard

## Frame 1
status: animated
src: index.html#opening
rules: staged-reveal, line-draw
beat: Three different robot types, one shared operational picture.

## Frame 2
status: animated
src: index.html#transport
rules: follow-through, callout-track
beat: The mobile robot accepts a bounded transport assignment and publishes live state.

## Frame 3
status: animated
src: index.html#inspection
rules: callout-track, status-transition
beat: The inspection robot verifies the destination while UMP shares health and battery.

## Frame 4
status: animated
src: index.html#handoff
rules: dependency-chain, status-transition
beat: The arm is released only after dependencies succeed.

## Frame 5
status: animated
src: index.html#close
rules: summary-lockup, staged-reveal
beat: UMP is the coordination layer, not the robot controller.


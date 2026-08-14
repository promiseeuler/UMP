# UMP Material Movement Profile v0.1

**Status:** Phase 3 draft

## Purpose

This profile coordinates movement of a logical physical subject between mobile bases, robot arms, conveyors, drones, storage systems, and human-operated stations without manufacturer-specific fields.

## Identifiers

- Subject: `ump:subject:<deployment-unique-name>`
- Transfer zone: `ump:resource:<site>:transfer-zone:<name>`
- Payload slot: `ump:resource:<machine>:payload-slot:<name>`
- Tool: `ump:resource:<machine>:tool:<name>`
- Frame: `ump:frame:<authority>:<name>`

## Capabilities

- `org.ump.material.transport` moves a subject to a declared spatial context.
- `org.ump.material.pick` secures a subject from a transfer context.
- `org.ump.material.place` releases a subject into a transfer context.
- `org.ump.material.handoff.source` participates as handoff source.
- `org.ump.material.handoff.destination` participates as handoff destination.

Capability inputs reference a subject ID, spatial context, reservation ID, and handoff ID. Payload, reach, precision, frame support, interruptibility, and retry policy are capability constraints rather than vendor model fields.

## Required resources

A material handoff MUST reserve the transfer zone and any exclusive source/destination payload slot or tool needed during transfer. Multi-resource claims are atomic. Implementations SHOULD acquire zone, destination slot, source slot, then tool by lexical resource ID through the core ordering rule.

## Spatial policy

All positions are metres and orientations radians/quaternions. Each deployment declares maximum pose age and uncertainty per capability. Unresolved frames and stale or over-uncertain poses block preparation. A profile implementation MUST NOT infer a transform from matching unqualified names.

## Completion evidence

The source evidence states that its controller no longer physically constrains the subject. Destination evidence states that its controller or fixture has positively secured the subject. Sensor-only proximity is insufficient unless deployment policy explicitly accepts that evidence type.

The source remains authoritative owner until both evidence records are durable and the handoff commits. Any uncertain physical result requires inspection and prohibits blind transfer retry.

## Safety boundary

This profile coordinates intent and evidence. Native controllers remain responsible for trajectory generation, collision avoidance, payload limits, guarded motion, emergency stopping, and local operator precedence.

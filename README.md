# Radtel RT-4D DDMR Builder

Reverse-engineering and documenting a reliable workflow for building `.ddmr` codeplug files for the Radtel RT-4D.

## Why this project exists

The RT-4D CPS does not reliably accept ordinary CHIRP-style CSV imports for complete codeplugs.

A CSV can contain the correct-looking information while still producing CPS data errors or placing values in the wrong fields. DMR codeplugs also contain linked information—channels, contacts, talkgroups, receive groups, and zones—not just independent rows in a spreadsheet.

The working approach is to preserve the structure of a valid CPS-generated `.ddmr` file and place new information into the fields expected by the RT-4D software.

## What the `.ddmr` file can contain

- Analog and digital channels
- DMR contacts and talkgroups
- Receive groups
- Zones
- Repeater frequencies
- Color codes
- Time slots
- Transmit power settings
- Other channel-specific options

## Confirmed workflow

1. Create or read a valid codeplug using the RT-4D CPS.
2. Save that file as the untouched backup.
3. Use a copy of the CPS-generated `.ddmr` file as the working base.
4. Insert or replace information while preserving the expected record layout.
5. Open the modified file in the CPS.
6. Check the affected channels, contacts, groups, and zones.
7. Confirm the CPS does not report data errors.
8. Save the validated file through the CPS.
9. Keep the last known-good file before making additional changes.

Starting with a CPS-safe file is important. Replacing the known-good base after each successful stage makes it easier to identify which change caused an error.

## How the records relate

- A **contact** identifies a DMR destination, such as a talkgroup.
- A **channel** contains frequencies and operating settings and can reference a contact.
- A **receive group** determines which digital contacts a channel can receive.
- A **zone** organizes existing channels into a usable group on the radio.

These records must point to the correct internal entries. Matching the visible text alone is not always enough.

## Validation

Before treating a modified `.ddmr` file as working, check:

- RX and TX frequencies
- Analog or digital mode
- Contact or talkgroup assignment
- Receive-group assignment
- Color code
- Time slot
- Transmit power
- Zone membership
- Any other fields changed during the build

A file is considered known-good only after it opens in the RT-4D CPS without unexpected data errors and the edited records still display correctly.

## Current status

This repository currently documents the working method and ongoing format investigation.

It does not yet contain a finished standalone builder. The project name reflects the intended direction: turning the proven `.ddmr` workflow into something repeatable and easier for other RT-4D owners to use.

## Planned additions

- A sanitized known-good `.ddmr` example
- Confirmed field and record mappings
- Input templates
- Repeatable validation tests
- Documentation of CPS-specific behavior
- Builder or conversion utilities

## Contributing

Information from other RT-4D users is welcome.

Useful reports should include:

- RT-4D CPS version
- Radio firmware version
- What operation was attempted
- The exact CPS error or unexpected behavior
- Whether the starting file came from the CPS, the radio, or another converter

Please remove personal information from files before sharing them.

# Agent HRM: design notes

**Subject.** The HR workspace of a small French company. Employees check time off and payslips,
managers approve leave, HR answers requests. Most visits are short: "what's my status, and what
needs me?" The design serves that glance first.

## Colour
| Token | Hex | Use |
|---|---|---|
| paper | `#F4F5F7` | app background (cool, not cream) |
| surface | `#FFFFFF` | content areas |
| ink | `#1A1E29` | text |
| ink-soft | `#5A6172` | secondary text |
| rule | `#E2E5EB` | borders, dividers |
| dossier | `#2B428F` | primary actions, links, selection (the blue of an HR file folder) |
| status | approved `#2E7A4E` · pending `#A86C14` · rejected `#B03A2E` · cancelled `#8A90A0` | only for status, never decoration |

## Type
IBM Plex Sans only. It reads like a well-kept document, and its tabular figures keep days and euros
aligned. Scale: 12 / 13 / 14 (body) / 16 / 20 / 28. Weights 400, 500, 600. Sentence case everywhere;
no all-caps labels.

## Layout
```
┌────────────┬──────────────────────────────────────────┬─────────────┐
│ Agent HRM  │ Page title                  bell  person │             │
│            ├──────────────────────────────────────────┤  Assistant  │
│ Me         │                                          │  (opens on  │
│  Home      │  content: left-aligned, max ~1080px      │   demand,   │
│  Time off  │  sections separated by space and rules,  │   420px)    │
│  Payslips  │  tables for lists, not stacks of cards   │             │
│  Requests  │                                          │             │
│ Team / HR  │                                          │             │
│ Company    │                                          │             │
└────────────┴──────────────────────────────────────────┴─────────────┘
```
On mobile the sidebar becomes a top menu and the assistant a full-screen sheet.

## The one bold element
**Days as tiles.** Leave is counted in whole days, so a balance is drawn as a row of small squares:
filled for days already approved, striped for days waiting for approval, open for days still
available. You read "4 left, 2 waiting" before reading a number. It appears on Home and Time off;
everything around it stays quiet.

## Other decisions
- Payslips look like a French *bulletin de paie*: a ledger with employee and employer columns and
  right-aligned tabular amounts, printable.
- Managers see "who else is off" next to every approval, the question they always ask.
- Status is a small coloured dot and a word, never a filled pill.
- The assistant proposes actions as confirmation cards; nothing changes until the person confirms.

## Checked against the generic defaults
- First draft used a teal primary; teal sat too close to "approved" green, so status became
  ambiguous. Changed to dossier blue.
- First draft put every home block in identical shadowed cards. Replaced with one bordered surface
  per area and rules inside it; the day tiles carry the visual weight.
- No gradients, no eyebrow labels, no monospace data labels, no arrows appended to buttons.

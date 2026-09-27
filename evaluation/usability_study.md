# Usability check — protocol

A short, informal task-based check with 3–5 participants. Its purpose is narrow:
to test whether people can **tell what they can see, what they can't, and why** —
the transparency requirement behind the ICO guidance the project is built on.
It is not a statistically powered study and the report should not present it as one.

## Before you start

- Participants should not have seen the application before.
- Explain: this is a university project, it takes about 10 minutes, no personal data
  is collected, their answers are anonymous, and they can stop at any time. Get verbal
  agreement and note it.
- Run `python manage.py seed_demo` so everyone starts from the same data.
- Don't help or hint during tasks. If someone is stuck for 2 minutes, record it as
  not completed and move on.

## Tasks

Read each task aloud. Record: completed (yes/no), time taken, and anything they say
or hesitate over.

| # | Signed in as | Task |
|---|---|---|
| T1 | `sales` | Open Alice Chen's record. Which pieces of her information can you see? |
| T2 | `sales` | There are fields you can't see. Pick one and tell me why you can't see it. |
| T3 | `bob` | Compare Alice's record with Dan's. Is there anything you can see for one but not the other? |
| T4 | `alice` | You don't want the Sales team to see your current project. Stop them. |
| T5 | `alice` | Find out who has looked at your record. |
| T6 | any | Find the page that explains what each department is allowed to see. |

## Questions afterwards

Ask on a 1–5 scale (1 = strongly disagree, 5 = strongly agree):

1. I could tell which information was hidden from me.
2. I understood why information was hidden.
3. I would trust this system to protect my own information.
4. Setting a privacy restriction on my own record was straightforward.

Then one open question: *"Was anything confusing?"*

## Recording sheet

| Participant | T1 | T2 | T3 | T4 | T5 | T6 | Q1 | Q2 | Q3 | Q4 | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| P1 |yes|no|yes|yes|yes|yes|5|3|4|4|It is never explicitly said why information is hidden|
| P2 |yes|yes|yes|yes|yes|yes|5|4|3|5|Information about hidden data is only shown if you hover over. That is hard to find if you do not know|
| P3 |yes|no|yes|yes|yes|yes|5|4|4|5|Nice to see who can see your personal data|
| P1 |yes|yes|yes|yes|yes|yes|5|5|4|4|Now you can see why data is hidden|
| P2 |yes|yes|yes|yes|yes|yes|5|5|3|5|The information about hidden data is now shown directly, great.|
| P3 |yes|yes|yes|yes|yes|yes|5|5|4|5|Nothing|

## What to look for

- **T2** is the key task. If people can see a field is hidden but can't say why,
  the redaction design is only half working — and that's a legitimate finding.
- **T3** tests whether the per-employee override is noticeable. It's deliberately
  subtle; people missing it is a reasonable result and says something about how
  visible individual restrictions should be.
- Note anything people *expect* to be able to do and can't.

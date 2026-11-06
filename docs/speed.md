# How fast is Rimuru, and what makes it faster

*Kimi models, Docker sandbox, 5 November 2026. Re-run with `python run.py speed`.*

## Why it was slow

A person asked for "a calculator with a web UI". One clone built it in **14 minutes**:
26,800 tokens written at **33 tokens per second**, more than half of it thinking (one
single think took 5 minutes), and 46,000 characters of code in 9 files - two test
suites, a server, a README and an icon. Time is simply *how much it writes* divided
by *how fast the model writes*.

## How fast each model writes

The same small page (a to-do list in one HTML file), asked of every model the key
can use (`python run.py speed probe`):

| Model | Seconds | Tokens/second |
|---|---|---|
| `kimi-k2.7-code-highspeed` | 14.9 | 219 |
| `kimi-k3.1-pro-highspeed` | 46.8 | 102 |
| `kimi-k2.7-code` | 22.5 | 74 |
| `kimi-k3` | 71.4 | 70 |
| `kimi-k2.6` | 87.0 | 45 |
| `kimi-k3.1-pro` (the clones' model) | 103.0 | 43 |

The clones were on the slowest model the key has.

## The benchmark

`python run.py speed run` sends three everyday requests through the real system -
Rimuru, a clone, the sandbox - each in a fresh folder, and checks the result:

| Task | Request | Counts as working when |
|---|---|---|
| web-calculator | "Build a calculator with a web UI" | a page with real controls and a script; its own tests pass when we run them |
| todo-page | "Make a simple to-do list page in a single HTML file" | one HTML file, script inline, an input to add items, no extra files needed |
| bug-fix | "The bulk discount looks wrong when someone orders exactly 10 of something" (in a small shop project) | hidden tests for 10 and 25 items pass, and nothing else broke |

## Results

Four setups ran the same three tasks (`python run.py speed compare`) - except the old
way, which finished only the calculator before the Kimi account ran out of credit
(see below). Rimuru itself was on `kimi-k3.1-pro-highspeed` in every run; only the
clones changed:

| Run | How clones work | Clones' model |
|---|---|---|
| before | thorough - everything they can think of, in one go (the old way) | `kimi-k3.1-pro` |
| quick | a first version first, extras offered afterwards (the new default) | `kimi-k3.1-pro` |
| quick+k3.1hs | a first version first | `kimi-k3.1-pro-highspeed` |
| quick+k2.7hs | a first version first | `kimi-k2.7-code-highspeed` |

| Task | Run | First reply | Tests first pass | Done | Tokens | Tokens/s | Thinking (chars) | Code (chars) | Works |
|---|---|---|---|---|---|---|---|---|---|
| web-calculator | before | 10s | - | **14.6 min** | 30,267 | 36 | 63,146 | 42,025 | yes |
| | quick | 9s | - | 5.9 min | 10,395 | 31 | 28,848 | 11,877 | yes |
| | quick+k3.1hs | 11s | - | 3.3 min | 14,646 | 81 | 38,352 | 19,624 | yes |
| | quick+k2.7hs | 9s | - | **1.9 min** | 13,169 | 150 | 27,999 | 12,204 | yes |
| todo-page | before | - | - | not finished\* | 18,479 so far | - | - | - | - |
| | quick | 8s | - | 4.8 min | 10,527 | 39 | 16,391 | 13,591 | yes |
| | quick+k3.1hs | 10s | - | 1.1 min | 5,080 | 95 | 8,763 | 7,274 | yes |
| | quick+k2.7hs | 13s | - | **36s** | 3,614 | 182 | 3,411 | 5,732 | yes |
| bug-fix | before | - | - | not run\* | - | - | - | - | - |
| | quick | 10s | 53s | 1.2 min | 1,555 | 28 | 2,565 | 350 | yes |
| | quick+k3.1hs | 9s | 30s | 41s | 1,848 | 67 | 3,398 | 266 | yes |
| | quick+k2.7hs | 11s | 20s | **25s** | 931 | 79 | 1,228 | 457 | yes |

\* **The old-way baseline for the to-do page and the bug fix was not measured: the Kimi
account ran out of credit in the middle of the run.** The `before` run was 10.7 minutes
and 18,479 tokens into the to-do page, still not done, when every call began failing
with HTTP 429 `exceeded_current_quota_error` ("insufficient balance"). It was stopped
there and could not be re-run. So the effect of the instruction change on its own is
shown on the calculator only.
"Tests first pass" is shown only where the clone ran tests through the sandbox's shell;
the builds ran theirs a different way, so the column is empty for them.

### What the instruction change did - same model

`before` to `quick`, both on `kimi-k3.1-pro`, on the calculator:

- **14.6 minutes became 5.9**: 2.5 times faster.
- It wrote **a third of the tokens** (30,267 to 10,395) and **a quarter of the code**
  (42,025 to 11,877 characters): 2 files instead of 8 - no README, no separate
  script and style files, no JavaScript test file the sandbox has no way to run.
- It **thought half as much** (63,146 to 28,848 characters) and took 12 turns, not 38.
- The model wrote no faster (31-36 tokens a second both times). All of the gain came
  from writing less.

### What the model change did - same instructions

From `quick` (on `kimi-k3.1-pro`) to the two fast models:

| Task | `kimi-k3.1-pro-highspeed` | `kimi-k2.7-code-highspeed` |
|---|---|---|
| web-calculator | 1.8 times faster (3.3 min) | 3.1 times faster (1.9 min) |
| todo-page | 4.3 times faster (1.1 min) | 8 times faster (36s) |
| bug-fix | 1.7 times faster (41s) | 2.8 times faster (25s) |

Both changes together took the calculator from **14.6 minutes to 1.9: 7.7 times
faster**. In real work the fast models wrote 80-180 tokens a second, less than in the
probe (102 and 219), because every turn also waits for the answer to start and for
the tools.

### Did quality hold?

- **Every task that finished works.** All three bug fixes pass the hidden tests for 10
  and 25 items, with nothing else broken. Every to-do page is one file needing
  nothing else. Every calculator has real controls and a script.
- **Tests vary.** The calculators came with 12 tests (`before`), 6 plus 6 sub-checks
  (`quick`), and 15 plus 20 sub-checks (`kimi-k3.1-pro-highspeed`). **The
  `kimi-k2.7-code-highspeed` calculator came with no tests at all** - only our check of
  the page vouches for it. On the bug fix it did run and pass 14 tests.
- **Smaller, not worse.** The quick versions dropped what nobody asked for (a README,
  extra files, a server). They gave up extras, not correctness.
- **What the checks cannot see.** No browser clicked through the pages: a page can pass
  and still have a bug in its JavaScript.
- **One wording problem.** Two finished messages pointed at `/workspace/todo.html` -
  the path inside the sandbox, which means nothing on the person's computer.

### Recommendation

*Since this benchmark, the quick way is the only way clones build - the old way was removed.*

**Put the clones on `kimi-k2.7-code-highspeed`.** It was the fastest on every task and
passed every check. Choose
`kimi-k3.1-pro-highspeed` instead if you would rather every build came with tests: it
was roughly half as fast, and it wrote the most tests. Keep `kimi-k3.1-pro` for work
where speed does not matter. Switch it in the app with **Models**, under Clones.

What is left: even on the fastest model, most of what a clone writes is thinking (28,000
characters of it for a 12,000-character calculator). A model or mode that thinks less,
showing the work as it is written, and helper clones for big jobs are the next levers.

### How far to trust these numbers

- **One run of each.** Model output varies from run to run; treat differences under
  about a third as noise.
- **The four runs ran side by side** on one Kimi account, which may have slowed each a
  little. The account then ran out of credit - which is what stopped the `before` run.
- **Same Rimuru in every run**: first replies took 8-13 seconds throughout, so the
  differences above are the clones.

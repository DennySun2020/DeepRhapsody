# NeuralDebug Agent: Python Debugging Example

This walkthrough demonstrates how to use the **NeuralDebug** agent to interactively debug a Python program using natural language.

## The Buggy Program

We'll debug `sample_buggy_grades.py` — a script that calculates student grade statistics (mean, median, standard deviation) but produces wrong results.

**Running the program shows:**

```
Valid scores: [92, 85, 0, 73, 88, 95, 67, 42]

Mean:   67.75
Median: 80.50
Std Dev: 29.63

Expected: mean=77.43, median=85.00, std_dev=18.50
Got:      mean=67.75, median=80.50, std_dev=29.63

*** RESULTS DO NOT MATCH EXPECTED VALUES ***
```

All three statistics are wrong. Let's use NeuralDebug to find out why.

---

## Example 1: Finding Bug #1 — Wrong Mean (Zero Included)

### Conversation

> **You:** Debug `sample_buggy_grades.py` — the mean is 67.75 but should be 77.43.

> **NeuralDebug:** I'll read the source code first to understand the structure...
>
> The program has these key functions:
> - `load_grades()` — returns 10 (name, score) tuples
> - `filter_valid_grades()` — filters to valid range
> - `calculate_mean()` — computes average
> - `generate_report()` — orchestrates everything
>
> The mean being too low suggests either too many scores are included (pulling the average down) or the sum is wrong. Let me start a debug session and look at the filtering logic.
>
> *Starting debug server...*
> *Setting breakpoint at line 44 (the filter condition)...*
> *Starting execution...*
>
> Paused at **line 44** in `filter_valid_grades()`.
> Current state:
> - `name = "Alice"`, `score = 92`
> - `valid = []` (empty — first iteration)
> - Condition: `score >= 0 and score <= 100`

> **You:** Step through the loop until we hit the suspicious entries.

> **NeuralDebug:** I'll step through each iteration...
>
> | Iteration | name | score | `score >= 0 and score <= 100` | Included? |
> |-----------|------|-------|-------------------------------|-----------|
> | 1 | Alice | 92 | `92 >= 0` → True | ✅ |
> | 2 | Bob | 85 | `85 >= 0` → True | ✅ |
> | 3 | Charlie | -1 | `-1 >= 0` → False | ❌ Correct |
> | 4 | Diana | 105 | `105 <= 100` → False | ❌ Correct |
> | **5** | **Eve** | **0** | **`0 >= 0` → True** | **✅ ← Problem!** |
> | 6 | Frank | 73 | True | ✅ |
>
> **Found it!** Eve's score of `0` is being included because the condition is `score >= 0`. According to the business rule, zero should be excluded — valid scores are 1–100 inclusive.

> **You:** What should the fix be?

> **NeuralDebug:** Change line 44 from:
> ```python
> if score >= 0 and score <= 100:
> ```
> to:
> ```python
> if score > 0 and score <= 100:
> ```
> This excludes zero, making the valid set `[92, 85, 73, 88, 95, 67, 42]` with a mean of **77.43** ✓.

---

## Example 2: Finding Bug #2 — Wrong Median (Unsorted Data)

### Conversation

> **You:** The median is 80.50 but should be 85.00. Can you check `calculate_median`?

> **NeuralDebug:** Let me set a breakpoint at line 60 where the median is computed.
>
> *Setting breakpoint at line 60...*
> *Continuing execution...*
>
> Paused at **line 60** in `calculate_median()`.
> - `scores = [92, 85, 0, 73, 88, 95, 67, 42]`
> - `n = 8`, `mid = 4`
>
> Since `n` is even, it computes `(scores[3] + scores[4]) / 2.0 = (73 + 88) / 2.0 = 80.50`.

> **You:** What's wrong with that?

> **NeuralDebug:** The list is **not sorted**! Median requires sorted data. The current order is the insertion order from `filter_valid_grades()`:
> ```
> Unsorted: [92, 85, 0, 73, 88, 95, 67, 42]
> Sorted:   [0, 42, 67, 73, 85, 88, 92, 95]
> ```
>
> With sorted data: `(73 + 85) / 2.0 = 79.0` (with the zero). After fixing Bug #1 (removing zero), the sorted list becomes `[42, 67, 73, 85, 88, 92, 95]` with 7 elements, so `median = sorted[3] = 85` ✓.
>
> **Fix:** Add `scores = sorted(scores)` at the beginning of `calculate_median()`.

---

## Example 3: Finding Bug #3 — Wrong Standard Deviation (N vs N-1)

### Conversation

> **You:** Standard deviation is also wrong. Debug `calculate_std_dev`.

> **NeuralDebug:** Let me set a breakpoint at line 72 where variance is computed.
>
> *Setting breakpoint at line 72...*
> *Continuing execution...*
>
> Paused at **line 72** in `calculate_std_dev()`.
> - `scores = [92, 85, 0, 73, 88, 95, 67, 42]`
> - `mean = 67.75`
> - `squared_diffs = [588.06, 297.56, 4590.06, ...]`
>
> Now let me evaluate the variance computation:
> ```python
> variance = sum(squared_diffs) / len(scores)    # divides by 8
> ```

> **You:** Shouldn't it divide by N-1?

> **NeuralDebug:** Exactly! For a **sample** standard deviation, you divide by `N - 1` (Bessel's correction), not `N`. The current code computes the **population** std dev.
>
> - Population variance: `Σ(xi - μ)² / N`
> - Sample variance: `Σ(xi - x̄)² / (N - 1)` ← correct for a sample
>
> **Fix:** Change line 72 from:
> ```python
> variance = sum(squared_diffs) / len(scores)
> ```
> to:
> ```python
> variance = sum(squared_diffs) / (len(scores) - 1)
> ```

> **You:** OK, stop debugging.

> **NeuralDebug:** *Quitting debug session...*
>
> ### Summary of Bugs Found
>
> | # | Location | Bug | Fix |
> |---|----------|-----|-----|
> | 1 | Line 44 | `score >= 0` includes zero | Change to `score > 0` |
> | 2 | Line 60 | Median computed on unsorted list | Add `scores = sorted(scores)` |
> | 3 | Line 72 | Divides by N (population σ) | Divide by N-1 (sample σ) |

---

## Example 4: Using `evaluate` to Test Hypotheses

### Conversation

> **You:** I think the grade distribution might also be wrong. Can you inspect it?

> **NeuralDebug:** Let me start a new session and set a breakpoint at line 100 inside the distribution loop.
>
> *Starting debug server...*
> *Setting breakpoint at line 100...*
> *Starting execution...*
>
> Paused at **line 100** in `generate_report()`. Current score is `92`.

> **You:** What does the `ranges` dictionary look like right now?

> **NeuralDebug:** Let me evaluate that expression.
>
> *Evaluating `ranges`...*
> ```python
> {'A (90-100)': 0, 'B (80-89)': 0, 'C (70-79)': 0, 'D (60-69)': 0, 'F (<60)': 0}
> ```
> All zeros — we haven't processed any scores yet (this is the first iteration).

> **You:** Continue to the end of the loop and check again.

> **NeuralDebug:** *Setting breakpoint after the loop at line 110...*
> *Continuing...*
>
> *Evaluating `ranges`...*
> ```python
> {'A (90-100)': 2, 'B (80-89)': 2, 'C (70-79)': 1, 'D (60-69)': 1, 'F (<60)': 2}
> ```
>
> With the buggy filter (including zero), zero falls into `F (<60)` giving `F=2`. After fixing Bug #1, the count becomes `F=1` (only `42`). The distribution logic itself is correct — it's cascading from the filter bug.

---

## Example 5: Investigating with Call Stack and `run_to_line`

### Conversation

> **You:** I want to understand how data flows from `load_grades()` into `calculate_mean()`. Can you trace it?

> **NeuralDebug:** Sure. I'll set a breakpoint inside `calculate_mean()` at line 53 and examine the call stack.
>
> *Setting breakpoint at line 53...*
> *Starting execution...*
>
> Paused at **line 53** in `calculate_mean()`.
>
> **Call Stack:**
> ```
> #0  calculate_mean()       at sample_buggy_grades.py:53
> #1  generate_report()      at sample_buggy_grades.py:92
> #2  main()                 at sample_buggy_grades.py:131
> #3  <module>               at sample_buggy_grades.py:149
> ```
>
> **Local Variables:**
> - `scores = [92, 85, 0, 73, 88, 95, 67, 42]`
>
> The flow is: `main()` → `generate_report()` → `calculate_mean()`. The scores list already includes the invalid zero, confirming the filter bug happens upstream.

> **You:** Jump to `generate_report` and show me where scores is built.

> **NeuralDebug:** Let me restart and run to line 90 in `generate_report()` where `scores` is assembled.
>
> *Restarting...*
> *Running to line 90...*
>
> Paused at **line 90** in `generate_report()`.
> ```python
> scores = [score for _, score in valid_grades]
> ```
>
> *Evaluating `valid_grades`...*
> ```python
> [('Alice', 92), ('Bob', 85), ('Eve', 0), ('Frank', 73),
>  ('Grace', 88), ('Hank', 95), ('Ivy', 67), ('Jack', 42)]
> ```
>
> There it is — `('Eve', 0)` is in the valid grades list. This is where the zero enters the pipeline. The root cause is in `filter_valid_grades()` at line 44.

---

## Quick Reference: Common Commands

| What you say | What happens |
|---|---|
| "Debug `script.py` — it gives wrong output" | Starts a debug session on the script |
| "Set a breakpoint at line 44" | Places a breakpoint |
| "Step through the loop" | Steps over line by line |
| "What is `x` right now?" | Evaluates the variable `x` |
| "What are all the local variables?" | Shows all variables in scope |
| "Show me the call stack" | Displays the full call chain |
| "Continue until the next breakpoint" | Resumes execution |
| "Run to line 90" | Runs until reaching line 90 |
| "Show the code around here" | Lists source code near current position |
| "Evaluate `len(my_list)`" | Runs an arbitrary Python expression |
| "Stop debugging" | Ends the session |

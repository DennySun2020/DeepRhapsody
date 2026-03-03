# NeuralDebug Agent: C# Debugging Example

This walkthrough demonstrates how to use the **NeuralDebug** agent to interactively debug a C# program using natural language. The agent uses **netcoredbg** (Samsung's open-source .NET debugger in MI mode) behind the scenes while you communicate in plain English.

## Platform Auto-Detection

Before debugging, the agent runs platform detection:

```
> python scripts/csharp_debug_session.py info
```
```json
{
  "platform": { "os": "win32", "os_name": "Windows", "arch": "AMD64" },
  "dotnet_sdk": {
    "version": "9.0.311",
    "path": "C:\\Program Files\\dotnet\\dotnet.exe"
  },
  "debugger": {
    "name": "netcoredbg",
    "path": "C:\\Users\\user\\.dotnet\\tools\\netcoredbg\\netcoredbg.exe",
    "version": "3.1.2"
  }
}
```

On **Linux** or **macOS** you'd see the platform-appropriate paths. The agent finds netcoredbg automatically.

## Auto-Build from Project

You can pass a `.csproj` file or project directory directly — the agent builds it with `dotnet build`:

```
> python scripts/csharp_debug_session.py serve sample_buggy_inventory/ --port 5679
Detected .NET project. Auto-building with debug symbols...
Building: dotnet build --configuration Debug
Built sample_buggy_inventory → bin/Debug/net8.0/sample_buggy_inventory.dll
Platform: win32 (AMD64)
Using debugger: netcoredbg (MI mode)
```

## The Buggy Program

We'll debug `sample_buggy_inventory` — a C# program that generates an inventory report (total value, average price, low stock count, most expensive product, category totals) but produces wrong results.

**Running the program shows:**

```
=== Inventory Report ===

Products in stock: 10
Total value:       $2949.91
Average price:     $294.00
Low stock items:   2
Most expensive:    Standing Desk

Category Totals:
  Electronics     $269.97
  Furniture       $0.00

--- Expected Values ---
Total value:       $50,569.00
Average price:     $327.77  (excluding discontinued)
Low stock items:   2  (Standing Desk, Webcam)
Most expensive:    Laptop ($999.99)
Electronics total: $33,169.05
Furniture total:   $17,399.95

*** BUG: Total value is $2949.91, expected $50,569.00 ***
*** BUG: Most expensive is 'Standing Desk', expected 'Laptop' ***
*** RESULTS DO NOT MATCH EXPECTED VALUES ***
```

Multiple values are wrong. Let's use NeuralDebug to find out why.

---

## Example 1: Finding Bug #1 — Total Value Missing Quantity Multiplier

### Conversation

> **You:** Debug `sample_buggy_inventory` — the total value is $2,949.91 but should be $50,569.00. That's way too low.

> **NeuralDebug:** The total value being ~58x too low suggests the quantity multiplier might be missing. Let me look at `ComputeTotalValue()`.
>
> *Starting debug server on the project...*
> *Setting breakpoint at `Program.cs:64` (inside ComputeTotalValue loop)...*
> *Starting execution...*
>
> Paused at **line 64** in `ComputeTotalValue()`.
> Current state:
> - `products.Count = 10`
> - `i = 0`
> - `total = 0`
>
> Let me step through the first few iterations:
>
> | Iteration | Product | `Price` | `Quantity` | Added to `total` | Note |
> |-----------|---------|---------|------------|------------------|------|
> | 1 | Laptop | 999.99 | 15 | 999.99 | ⚠️ Should be 14,999.85 |
> | 2 | Mouse | 29.99 | 150 | 29.99 | ⚠️ Should be 4,498.50 |
> | 3 | Keyboard | 79.99 | 85 | 79.99 | ⚠️ Should be 6,799.15 |
>
> **Found it!** Line 66 reads:
> ```csharp
> total += products[i].Price;
> ```
> It adds only the price, ignoring quantity entirely. The total is just the sum of all unit prices.

> **You:** What's the fix?

> **NeuralDebug:** Change line 66 from:
> ```csharp
> total += products[i].Price;
> ```
> to:
> ```csharp
> total += products[i].Price * products[i].Quantity;
> ```
> Now: Laptop contributes `999.99 × 15 = 14,999.85`, and the grand total becomes **$50,569.00** ✓.

---

## Example 2: Finding Bug #2 — Integer Division in Average Price

### Conversation

> **You:** The average price shows $294.00 but should be $327.77. It's also a suspiciously round number.

> **NeuralDebug:** A round number average is a classic sign of integer division. Let me check `ComputeAveragePrice()`.
>
> *Setting breakpoint at `Program.cs:82`...*
> *Continuing execution...*
>
> Paused at **line 82** in `ComputeAveragePrice()`.
> - `sum = 0` (type: `int`)
> - `count = 10` (type: `int`)
>
> Let me step through and evaluate:
>
> ```
> (netcoredbg) -data-evaluate-expression sum
>  → 2940 (int!)
> (netcoredbg) -data-evaluate-expression count
>  → 10 (int)
> (netcoredbg) -data-evaluate-expression sum / count
>  → 294 (integer division!)
> ```
>
> **Two bugs found:**
>
> **Bug 2a:** `sum` is declared as `int`, so `(int)products[i].Price` truncates the decimal: `(int)999.99 = 999`, `(int)29.99 = 29`, etc. The `int` sum is 2940 instead of the correct 2949.91.
>
> **Bug 2b:** `sum / count` is **integer division** (both are `int`): `2940 / 10 = 294` instead of `294.0`. Even if the sum were correct, the division would truncate.
>
> **Bug 2c:** Zero-price discontinued items (Lamp, price=0.00) are included in the count, pulling the average down. The expected value $327.77 uses count=9 (excluding zero-price items).

> **You:** How should I fix all three issues?

> **NeuralDebug:** Replace the method with:
> ```csharp
> public static double ComputeAveragePrice(List<Product> products)
> {
>     double sum = 0;
>     int count = 0;
>     for (int i = 0; i < products.Count; i++)
>     {
>         if (products[i].Price > 0)  // exclude discontinued
>         {
>             sum += products[i].Price;  // no int cast
>             count++;
>         }
>     }
>     return count > 0 ? sum / count : 0;  // double division
> }
> ```
> Now: `sum = 2949.91`, `count = 9`, `2949.91 / 9 = 327.77` ✓.

---

## Example 3: Finding Bug #3 — Off-by-One in Low Stock Count

### Conversation

> **You:** The low stock count happens to show 2, which matches expectations. But I suspect there's a latent bug — can you check `CountLowStock()`?

> **NeuralDebug:** Good instinct. Let me examine the loop boundaries.
>
> *Setting breakpoint at `Program.cs:96`...*
> *Continuing execution...*
>
> Paused at `CountLowStock()`. Let me evaluate the loop condition:
>
> ```csharp
> for (int i = 0; i < products.Count - 1; i++)
> ```
>
> `products.Count = 10`, so this iterates `i = 0..8` — **9 iterations for 10 products**. The last product (index 9, "Lamp") is never checked!
>
> Let me verify by stepping through:
>
> | Index | Product | Quantity | `< 10`? | Counted? |
> |-------|---------|----------|---------|----------|
> | 0 | Laptop | 15 | No | ❌ |
> | 1 | Mouse | 150 | No | ❌ |
> | ... | ... | ... | ... | ... |
> | 4 | Standing Desk | 8 | Yes | ✅ |
> | 7 | Webcam | 3 | Yes | ✅ |
> | 8 | Bookshelf | 12 | No | ❌ |
> | **9** | **Lamp** | **45** | **—** | **⚠️ Never checked!** |
>
> The count is accidentally correct (2) because the skipped item (Lamp, qty=45) wouldn't qualify anyway. But if the last product had low stock, it would be missed.

> **You:** What's the fix?

> **NeuralDebug:** Change line 96 from:
> ```csharp
> for (int i = 0; i < products.Count - 1; i++)
> ```
> to:
> ```csharp
> for (int i = 0; i < products.Count; i++)
> ```
> Remove the `- 1` to iterate over all products. This is a classic off-by-one that's especially dangerous because it only manifests when the last element meets the condition.

---

## Example 4: Finding Bug #4 — Most Expensive Skips First Product

### Conversation

> **You:** The most expensive product shows "Standing Desk" ($599.99) but should be "Laptop" ($999.99). Debug `FindMostExpensive`.

> **NeuralDebug:** Let me check the loop in `FindMostExpensive()`.
>
> *Setting breakpoint at `Program.cs:110`...*
> *Continuing execution...*
>
> Paused at `FindMostExpensive()`. Inspecting the loop:
>
> ```csharp
> for (int i = 1; i < products.Count; i++)
> ```
>
> **Found it!** The loop starts at `i = 1`, **skipping `products[0]` which is Laptop ($999.99)**. It only compares products 1–9, finding Standing Desk ($599.99) as the maximum among those.
>
> Let me verify:
> ```
> (netcoredbg) -data-evaluate-expression products[0].Name
>  → "Laptop"
> (netcoredbg) -data-evaluate-expression products[0].Price
>  → 999.99
> ```
>
> The most expensive product is at index 0 and is completely ignored.

> **You:** And I see `mostExpensive` could be null if the list is empty?

> **NeuralDebug:** Yes, there's a secondary issue. `mostExpensive` is initialized to `null` and the `!` (null-forgiving operator) on the return suppresses the compiler warning but would throw `NullReferenceException` at runtime if the list were empty.
>
> **Fix:** Change the loop to start at index 0, and handle the empty list:
> ```csharp
> public static string FindMostExpensive(List<Product> products)
> {
>     if (products.Count == 0) return "(none)";
>     
>     double maxPrice = products[0].Price;
>     string mostExpensive = products[0].Name;
>
>     for (int i = 1; i < products.Count; i++)
>     {
>         if (products[i].Price > maxPrice)
>         {
>             maxPrice = products[i].Price;
>             mostExpensive = products[i].Name;
>         }
>     }
>     return mostExpensive;
> }
> ```
> Initialize from `products[0]`, then compare from index 1 onward. Now Laptop ($999.99) is correctly identified ✓.

---

## Example 5: Finding Bug #5 — Category Totals Overwrite Instead of Accumulate

### Conversation

> **You:** The category totals show Electronics=$269.97 and Furniture=$0.00. Those should be $33,169.05 and $17,399.95. Something is very wrong.

> **NeuralDebug:** Let me look at `ComputeCategoryTotals()`.
>
> *Setting breakpoint at `Program.cs:130`...*
> *Continuing execution...*
>
> Paused inside the `foreach` loop. Let me step through the Electronics products:
>
> | Product | `value` (Price × Qty) | `totals["Electronics"]` after |
> |---------|----------------------|-------------------------------|
> | Laptop | 14,999.85 | 14,999.85 |
> | Mouse | 4,498.50 | **4,498.50** ← Overwrote! |
> | Keyboard | 6,799.15 | **6,799.15** ← Overwrote! |
> | Monitor | 13,499.70 | **13,499.70** ← Overwrote! |
> | Headphones | 11,999.40 | **11,999.40** ← Overwrote! |
> | Webcam | 269.97 | **269.97** ← Overwrote! |
>
> **Found it!** Line 133 reads:
> ```csharp
> totals[product.Category] = value;   // overwrites!
> ```
> When the category already exists, it **replaces** the total instead of **adding** to it. So we only see the value of the *last* product in each category (Webcam for Electronics, Lamp for Furniture — and Lamp has price $0.00).

> **You:** What's the fix?

> **NeuralDebug:** Change line 133 from:
> ```csharp
> totals[product.Category] = value;
> ```
> to:
> ```csharp
> totals[product.Category] += value;
> ```
> Use `+=` to accumulate. Now Electronics = $33,169.05 ✓ and Furniture = $17,399.95 ✓.

---

## Example 6: Using Variable Inspection and Expression Evaluation

### Conversation

> **You:** I want to check the full product list and verify the data at runtime.

> **NeuralDebug:** I'll set a breakpoint right after loading inventory and inspect the data.
>
> *Setting breakpoint at `Program.cs:155` (after LoadInventory call)...*
> *Starting execution...*
>
> Paused at **line 155**. Let me evaluate:
>
> ```
> (netcoredbg) -data-evaluate-expression inventory.Count
>  → 10
> (netcoredbg) -data-evaluate-expression inventory[0]
>  → {SampleBuggyInventory.Product} Name="Laptop" Price=999.99 Quantity=15 Category="Electronics"
> (netcoredbg) -data-evaluate-expression inventory.Where(p => p.Quantity < 10).Count()
>  → 2
> (netcoredbg) -data-evaluate-expression inventory.Sum(p => p.Price * p.Quantity)
>  → 50569.0
> ```
>
> Using LINQ expressions in the debugger confirms the expected total is $50,569.00. The data is correct — it's the computation methods that have bugs.

> **You:** Show me all low-stock items.

> **NeuralDebug:**
> ```
> (netcoredbg) -data-evaluate-expression inventory.Where(p => p.Quantity < 10).Select(p => p.Name)
>  → {"Standing Desk", "Webcam"}
> ```
>
> Two products have stock below 10: Standing Desk (8) and Webcam (3) ✓.

> **You:** Got it, stop the session.

> **NeuralDebug:** *Quitting debug session...*
>
> ### Summary of Bugs Found
>
> | # | Location | Bug | Fix |
> |---|----------|-----|-----|
> | 1 | `ComputeTotalValue` line 66 | Adds `Price` instead of `Price * Quantity` | Multiply by `Quantity` |
> | 2 | `ComputeAveragePrice` lines 78–86 | `int` truncation + integer division + includes zero-price items | Use `double`, filter zeros |
> | 3 | `CountLowStock` line 96 | `Count - 1` skips last product | Change to `Count` |
> | 4 | `FindMostExpensive` line 111 | Loop starts at `i=1`, skipping first product | Initialize from `[0]`, loop from 1 |
> | 5 | `ComputeCategoryTotals` line 133 | Assignment `=` instead of `+=` overwrites totals | Use `+=` to accumulate |

---

## Quick Reference: Common Commands

| What you say | What happens |
|---|---|
| "Debug `MyProject/` — it crashes" | Builds and starts a debug session |
| "Debug `MyApp.dll`" | Starts debugging a pre-compiled DLL |
| "Break at `Program.cs` line 42" | Sets a breakpoint at a specific file and line |
| "Break at `MyClass.MyMethod`" | Sets a breakpoint at a method |
| "Step into that method" | Steps into the next method call |
| "Step over" / "Next line" | Executes the current line |
| "Step out" | Runs until the current method returns |
| "Continue" | Resumes until next breakpoint |
| "What is `inventory.Count`?" | Evaluates and prints the expression |
| "Evaluate `items.Where(x => x.Price > 100)`" | Evaluates a LINQ expression |
| "Show the local variables" | Shows all locals in current scope |
| "Show the call stack" | Displays backtrace |
| "Remove breakpoint at line 42" | Removes a breakpoint |
| "Stop debugging" | Ends the session |

## Tips for C# Debugging

- **Build with `Debug` configuration** to ensure full debug symbols and no compiler optimizations.
- **Watch for common C# bugs**: integer division (`int / int`), null reference exceptions, off-by-one loops, LINQ deferred execution surprises, and `=` vs `+=` in accumulators.
- **Use LINQ in the debugger** — netcoredbg supports evaluating LINQ expressions at breakpoints, which is great for inspecting collections.
- **Check nullable reference types** — even with `<Nullable>enable</Nullable>`, the compiler warnings can be suppressed with `!` (null-forgiving), hiding real bugs.
- **Dictionary operations** — `dict[key] = value` silently overwrites; use `dict[key] += value` or check-and-add patterns for accumulation.
- **Type casting** — explicit casts like `(int)doubleValue` silently truncate; prefer `Math.Round()` or keep the `double` type throughout.

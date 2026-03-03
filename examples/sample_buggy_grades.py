"""
Sample buggy script for testing the NeuralDebug agent.

This script calculates statistics (mean, median, std deviation) for student
grades, but contains several bugs that cause incorrect results.

Expected behavior:
  - Read grades from a list of (name, score) tuples
  - Filter out invalid scores (negative or > 100)
  - Calculate mean, median, and standard deviation
  - Print a summary report

Bugs planted:
  1. Line 30: Off-by-one in filtering logic (uses >= 0 but should be > 0 for
     the "exclude zeros" business rule)
  2. Line 45: Median calculation doesn't sort the list first
  3. Line 55: Standard deviation divides by N instead of N-1 (sample vs population)
"""

import math


def load_grades():
    """Return sample student grades as (name, score) tuples."""
    return [
        ("Alice", 92),
        ("Bob", 85),
        ("Charlie", -1),       # invalid: negative
        ("Diana", 105),        # invalid: > 100
        ("Eve", 0),            # edge case: zero (should be excluded per business rule)
        ("Frank", 73),
        ("Grace", 88),
        ("Hank", 95),
        ("Ivy", 67),
        ("Jack", 42),
    ]


def filter_valid_grades(grades):
    """Filter out invalid grades. Valid means 1-100 inclusive."""
    valid = []
    for name, score in grades:
        # BUG: should be `score > 0` to exclude zeros per business rule
        if score > 0 and score <= 100:
            valid.append((name, score))
    return valid


def calculate_mean(scores):
    """Calculate the arithmetic mean."""
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def calculate_median(scores):
    """Calculate the median value."""
    if not scores:
        return 0.0
    scores = sorted(scores)
    n = len(scores)
    mid = n // 2
    if n % 2 == 0:
        return (scores[mid - 1] + scores[mid]) / 2.0
    else:
        return float(scores[mid])


def calculate_std_dev(scores, mean):
    """Calculate standard deviation."""
    if len(scores) < 2:
        return 0.0
    squared_diffs = [(s - mean) ** 2 for s in scores]
    variance = sum(squared_diffs) / (len(scores) - 1)
    return math.sqrt(variance)


def generate_report(grades):
    """Generate a statistics report for the given grades."""
    print("=" * 40)
    print("  Student Grade Statistics Report")
    print("=" * 40)

    # Step 1: Filter
    valid_grades = filter_valid_grades(grades)
    print(f"\nTotal students: {len(grades)}")
    print(f"Valid grades:   {len(valid_grades)}")
    print(f"Excluded:       {len(grades) - len(valid_grades)}")

    # Step 2: Extract scores
    scores = [score for _, score in valid_grades]
    print(f"\nValid scores: {scores}")

    # Step 3: Calculate statistics
    mean = calculate_mean(scores)
    median = calculate_median(scores)
    std_dev = calculate_std_dev(scores, mean)

    print(f"\nMean:   {mean:.2f}")
    print(f"Median: {median:.2f}")
    print(f"Std Dev: {std_dev:.2f}")

    # Step 4: Grade distribution
    print("\nGrade Distribution:")
    ranges = {"A (90-100)": 0, "B (80-89)": 0, "C (70-79)": 0,
              "D (60-69)": 0, "F (<60)": 0}
    for score in scores:
        if score >= 90:
            ranges["A (90-100)"] += 1
        elif score >= 80:
            ranges["B (80-89)"] += 1
        elif score >= 70:
            ranges["C (70-79)"] += 1
        elif score >= 60:
            ranges["D (60-69)"] += 1
        else:
            ranges["F (<60)"] += 1

    for grade_range, count in ranges.items():
        bar = "#" * count
        print(f"  {grade_range}: {count} {bar}")

    print("\n" + "=" * 40)
    return mean, median, std_dev


def main():
    grades = load_grades()
    mean, median, std_dev = generate_report(grades)

    # These are the EXPECTED correct values (after fixing all bugs):
    #   Valid scores (excluding 0, negatives, >100): [92, 85, 73, 88, 95, 67, 42]
    #   Mean:   77.43
    #   Median: 85.00  (sorted: [42, 67, 73, 85, 88, 92, 95])
    #   Std Dev (sample): 18.50

    print("\nExpected: mean=77.43, median=85.00, std_dev=18.50")
    print(f"Got:      mean={mean:.2f}, median={median:.2f}, std_dev={std_dev:.2f}")

    if abs(mean - 77.43) > 0.1 or abs(median - 85.0) > 0.1 or abs(std_dev - 18.50) > 0.1:
        print("\n*** RESULTS DO NOT MATCH EXPECTED VALUES ***")
    else:
        print("\n✓ All values match!")


if __name__ == "__main__":
    main()

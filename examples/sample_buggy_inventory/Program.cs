using System;
using System.Collections.Generic;
using System.Linq;

namespace SampleBuggyInventory
{
    public class Product
    {
        public string Name { get; set; } = "";
        public double Price { get; set; }
        public int Quantity { get; set; }
        public string Category { get; set; } = "";
    }

    public class InventoryReport
    {
        public int TotalProducts { get; set; }
        public double TotalValue { get; set; }
        public double AveragePrice { get; set; }
        public int LowStockCount { get; set; }
        public string MostExpensiveProduct { get; set; } = "";
        public Dictionary<string, double> CategoryTotals { get; set; } = new();
    }

    public static class InventoryAnalyzer
    {
        /// <summary>
        /// Load sample inventory data.
        /// </summary>
        public static List<Product> LoadInventory()
        {
            return new List<Product>
            {
                new Product { Name = "Laptop",      Price = 999.99, Quantity = 15, Category = "Electronics" },
                new Product { Name = "Mouse",        Price = 29.99,  Quantity = 150, Category = "Electronics" },
                new Product { Name = "Keyboard",     Price = 79.99,  Quantity = 85,  Category = "Electronics" },
                new Product { Name = "Desk Chair",   Price = 349.99, Quantity = 22,  Category = "Furniture" },
                new Product { Name = "Standing Desk", Price = 599.99, Quantity = 8,   Category = "Furniture" },
                new Product { Name = "Monitor",      Price = 449.99, Quantity = 30,  Category = "Electronics" },
                new Product { Name = "Headphones",   Price = 199.99, Quantity = 60,  Category = "Electronics" },
                new Product { Name = "Webcam",       Price = 89.99,  Quantity = 3,   Category = "Electronics" },
                new Product { Name = "Bookshelf",    Price = 149.99, Quantity = 12,  Category = "Furniture" },
                new Product { Name = "Lamp",         Price = 0.00,   Quantity = 45,  Category = "Furniture" }, // discontinued, price=0
            };
        }

        /// <summary>
        /// BUG #1: Computes total inventory value but uses price instead of price*quantity.
        /// Should be sum of (price * quantity) for each product.
        /// Expected: 999.99*15 + 29.99*150 + 79.99*85 + 349.99*22 + 599.99*8 + 449.99*30 +
        ///           199.99*60 + 89.99*3 + 149.99*12 + 0*45 = 50,569.00
        /// Actual (buggy): sums only prices = 2,949.91
        /// </summary>
        public static double ComputeTotalValue(List<Product> products)
        {
            double total = 0;
            for (int i = 0; i < products.Count; i++)
            {
                // BUG: should be products[i].Price * products[i].Quantity
                total += products[i].Price;
            }
            return total;
        }

        /// <summary>
        /// BUG #2: Computes average price but uses integer division.
        /// Expected: 2949.91 / 10 = 294.99 (excluding zero-price items: 2949.91 / 9 = 327.77)
        /// Actual (buggy): uses Count as int divisor, producing truncated result.
        /// Also includes zero-price discontinued items in the average.
        /// </summary>
        public static double ComputeAveragePrice(List<Product> products)
        {
            int sum = 0;
            int count = products.Count;
            for (int i = 0; i < products.Count; i++)
            {
                // BUG: casting double to int truncates decimal part
                sum += (int)products[i].Price;
            }
            // BUG: integer division (sum and count are both int)
            // Also includes zero-price items in count
            return sum / count;
        }

        /// <summary>
        /// BUG #3: Counts "low stock" products (quantity &lt; 10) but uses wrong comparison.
        /// Expected low stock: Standing Desk (8), Webcam (3) → count = 2
        /// Actual (buggy): uses &lt;= 10, so also counts items with exactly 10 → still 2 here,
        ///   but the real bug is it skips the last product in the list (off-by-one).
        /// </summary>
        public static int CountLowStock(List<Product> products, int threshold = 10)
        {
            int count = 0;
            // BUG: iterates to Count - 1, missing the last product
            for (int i = 0; i < products.Count - 1; i++)
            {
                if (products[i].Quantity < threshold)
                {
                    count++;
                }
            }
            return count;
        }

        /// <summary>
        /// BUG #4: Finds the most expensive product but has a null reference risk
        /// and an incorrect initial comparison value.
        /// </summary>
        public static string FindMostExpensive(List<Product> products)
        {
            // BUG: initializes maxPrice to 0, so products with price 0 won't update,
            // and if all products had negative prices (hypothetically), this would fail.
            // But the real bug: starts loop at i=1, skipping products[0] entirely.
            double maxPrice = 0;
            string? mostExpensive = null;

            for (int i = 1; i < products.Count; i++)
            {
                if (products[i].Price > maxPrice)
                {
                    maxPrice = products[i].Price;
                    mostExpensive = products[i].Name;
                }
            }

            // BUG: no null check — if list were empty, this would throw NullReferenceException
            return mostExpensive!;
        }

        /// <summary>
        /// BUG #5: Computes category totals but accumulates incorrectly — 
        /// uses assignment instead of addition when category already exists.
        /// </summary>
        public static Dictionary<string, double> ComputeCategoryTotals(List<Product> products)
        {
            var totals = new Dictionary<string, double>();

            foreach (var product in products)
            {
                double value = product.Price * product.Quantity;
                if (totals.ContainsKey(product.Category))
                {
                    // BUG: overwrites instead of accumulating
                    totals[product.Category] = value;
                }
                else
                {
                    totals[product.Category] = value;
                }
            }

            return totals;
        }

        /// <summary>
        /// Generate the full inventory report.
        /// </summary>
        public static InventoryReport GenerateReport(List<Product> products)
        {
            return new InventoryReport
            {
                TotalProducts = products.Count,
                TotalValue = ComputeTotalValue(products),
                AveragePrice = ComputeAveragePrice(products),
                LowStockCount = CountLowStock(products),
                MostExpensiveProduct = FindMostExpensive(products),
                CategoryTotals = ComputeCategoryTotals(products),
            };
        }
    }

    class Program
    {
        static void Main(string[] args)
        {
            var inventory = InventoryAnalyzer.LoadInventory();
            var report = InventoryAnalyzer.GenerateReport(inventory);

            Console.WriteLine("=== Inventory Report ===");
            Console.WriteLine();
            Console.WriteLine($"Products in stock: {report.TotalProducts}");
            Console.WriteLine($"Total value:       ${report.TotalValue:F2}");
            Console.WriteLine($"Average price:     ${report.AveragePrice:F2}");
            Console.WriteLine($"Low stock items:   {report.LowStockCount}");
            Console.WriteLine($"Most expensive:    {report.MostExpensiveProduct}");
            Console.WriteLine();

            Console.WriteLine("Category Totals:");
            foreach (var (category, total) in report.CategoryTotals)
            {
                Console.WriteLine($"  {category,-15} ${total:F2}");
            }

            Console.WriteLine();
            Console.WriteLine("--- Expected Values ---");
            Console.WriteLine("Total value:       $50,569.00");
            Console.WriteLine("Average price:     $327.77  (excluding discontinued)");
            Console.WriteLine("Low stock items:   2  (Standing Desk, Webcam)");
            Console.WriteLine("Most expensive:    Laptop ($999.99)");
            Console.WriteLine("Electronics total: $33,169.05");
            Console.WriteLine("Furniture total:   $17,399.95");

            // Verify
            bool hasErrors = false;
            if (Math.Abs(report.TotalValue - 50569.00) > 0.01)
            {
                Console.WriteLine($"\n*** BUG: Total value is ${report.TotalValue:F2}, expected $50,569.00 ***");
                hasErrors = true;
            }
            if (report.LowStockCount != 2)
            {
                Console.WriteLine($"\n*** BUG: Low stock count is {report.LowStockCount}, expected 2 ***");
                hasErrors = true;
            }
            if (report.MostExpensiveProduct != "Laptop")
            {
                Console.WriteLine($"\n*** BUG: Most expensive is '{report.MostExpensiveProduct}', expected 'Laptop' ***");
                hasErrors = true;
            }
            if (!hasErrors)
            {
                Console.WriteLine("\n✓ All values match expected results.");
            }
            else
            {
                Console.WriteLine("\n*** RESULTS DO NOT MATCH EXPECTED VALUES ***");
            }
        }
    }
}

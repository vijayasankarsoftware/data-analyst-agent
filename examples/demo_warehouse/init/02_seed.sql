-- Deterministic synthetic data: setseed() makes random() repeatable,
-- so everyone gets the same numbers (important for evals in phase 9).
SELECT setseed(0.42);

-- 500 customers. The CTE picks one index per row so city and country stay consistent.
WITH c AS (
    SELECT g, 1 + floor(random() * 8)::int AS i
    FROM generate_series(1, 500) AS g
)
INSERT INTO customers (name, email, city, country, segment, signup_date)
SELECT
    'Customer ' || g,
    'customer' || g || '@example.com',
    (ARRAY['Mumbai', 'Delhi', 'Bengaluru', 'Chennai', 'London', 'New York', 'Berlin', 'Singapore'])[i],
    (ARRAY['India',  'India', 'India',     'India',   'UK',     'USA',      'Germany', 'Singapore'])[i],
    (ARRAY['consumer', 'corporate', 'small_business'])[1 + floor(random() * 3)::int],
    DATE '2023-01-01' + floor(random() * 365)::int
FROM c;

-- 120 products across 6 categories, cost = 40-70% of price.
WITH p AS (
    SELECT g,
           1 + floor(random() * 6)::int AS i,
           round((10 + random() * 190)::numeric, 2) AS price
    FROM generate_series(1, 120) AS g
)
INSERT INTO products (sku, name, category, unit_price, unit_cost)
SELECT
    'SKU-' || lpad(g::text, 4, '0'),
    (ARRAY['Shirt', 'Trousers', 'Dress', 'Shoes', 'Accessory', 'Jacket'])[i] || ' #' || g,
    (ARRAY['Shirts', 'Trousers', 'Dresses', 'Shoes', 'Accessories', 'Outerwear'])[i],
    price,
    round(price * (0.4 + random() * 0.3)::numeric, 2)
FROM p;

-- 5,000 orders spread across 2024-01-01 .. ~2025-10.
WITH o AS (
    SELECT g, random() AS r
    FROM generate_series(1, 5000) AS g
)
INSERT INTO orders (customer_id, order_date, status, channel)
SELECT
    1 + floor(random() * 500)::int,
    TIMESTAMPTZ '2024-01-01' + random() * INTERVAL '660 days',
    CASE WHEN r < 0.70 THEN 'delivered'
         WHEN r < 0.82 THEN 'shipped'
         WHEN r < 0.90 THEN 'placed'
         WHEN r < 0.96 THEN 'returned'
         ELSE 'cancelled' END,
    (ARRAY['web', 'mobile', 'store'])[1 + floor(random() * 3)::int]
FROM o;

-- 1-4 line items per order.
INSERT INTO order_items (order_id, product_id, quantity, unit_price, discount)
SELECT x.order_id, p.id, x.qty, p.unit_price, x.discount
FROM (
    SELECT o.id AS order_id,
           1 + floor(random() * 120)::int AS product_id,
           1 + floor(random() * 3)::int   AS qty,
           (ARRAY[0, 0, 0, 0.05, 0.10, 0.20])[1 + floor(random() * 6)::int] AS discount
    FROM orders AS o
    JOIN generate_series(1, 4) AS s ON s <= o.id % 4 + 1
) AS x
JOIN products AS p ON p.id = x.product_id;

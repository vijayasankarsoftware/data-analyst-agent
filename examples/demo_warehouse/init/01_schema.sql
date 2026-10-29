-- DEMO DATA SOURCE: a sample retail warehouse.
-- The agent knows nothing about these tables in advance. It discovers them at runtime,
-- exactly like a database a user plugs in. We use this one for tests, evals and demos.

CREATE TABLE customers (
    id           serial PRIMARY KEY,
    name         text        NOT NULL,
    email        text        NOT NULL UNIQUE,
    city         text        NOT NULL,
    country      text        NOT NULL,
    segment      text        NOT NULL CHECK (segment IN ('consumer', 'corporate', 'small_business')),
    signup_date  date        NOT NULL
);

CREATE TABLE products (
    id          serial PRIMARY KEY,
    sku         text           NOT NULL UNIQUE,
    name        text           NOT NULL,
    category    text           NOT NULL,
    unit_price  numeric(10, 2) NOT NULL CHECK (unit_price > 0),
    unit_cost   numeric(10, 2) NOT NULL CHECK (unit_cost > 0)
);

CREATE TABLE orders (
    id           serial PRIMARY KEY,
    customer_id  int         NOT NULL REFERENCES customers (id),
    order_date   timestamptz NOT NULL,
    status       text        NOT NULL CHECK (status IN ('placed', 'shipped', 'delivered', 'returned', 'cancelled')),
    channel      text        NOT NULL CHECK (channel IN ('web', 'mobile', 'store'))
);

CREATE TABLE order_items (
    id          serial PRIMARY KEY,
    order_id    int            NOT NULL REFERENCES orders (id),
    product_id  int            NOT NULL REFERENCES products (id),
    quantity    int            NOT NULL CHECK (quantity > 0),
    unit_price  numeric(10, 2) NOT NULL,   -- price at time of sale (can differ from products.unit_price)
    discount    numeric(4, 3)  NOT NULL DEFAULT 0 CHECK (discount >= 0 AND discount < 1)
);

CREATE INDEX ON orders (customer_id);
CREATE INDEX ON orders (order_date);
CREATE INDEX ON order_items (order_id);
CREATE INDEX ON order_items (product_id);

-- Column comments are discovered by the SQL agent during schema introspection (phase 2).
-- Well-documented sources get far better answers. User databases often lack comments,
-- so phase 3 lets users upload a data dictionary as a document instead.
COMMENT ON TABLE  orders IS 'One row per customer order. Revenue should exclude cancelled orders.';
COMMENT ON COLUMN orders.status IS 'placed -> shipped -> delivered; returned and cancelled are terminal.';
COMMENT ON COLUMN order_items.discount IS 'Fraction off unit_price, e.g. 0.10 = 10% off. Line revenue = quantity * unit_price * (1 - discount).';
COMMENT ON COLUMN products.unit_cost IS 'Cost to us per unit. Margin = unit_price - unit_cost.';

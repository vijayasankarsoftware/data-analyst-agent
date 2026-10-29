# Analytics Data Dictionary

Business definitions used in all reporting. When numbers disagree, these definitions win.

## Revenue

Revenue of an order line = `quantity * unit_price * (1 - discount)` from `order_items`.
**Cancelled orders are excluded from revenue.** Returned orders are included in gross revenue
and subtracted only in *net revenue*.

## Average order value (AOV)

Revenue divided by the number of distinct non-cancelled orders in the same period.

## Active customer

A customer who placed at least one non-cancelled order in the **last 90 days** of the period
being analysed.

## Return rate

Number of orders with status `returned` divided by all orders in the period (including
cancelled), expressed as a fraction between 0 and 1.

## Gross margin

`(order_items.unit_price * (1 - discount) - products.unit_cost) * quantity`.

## Channels

`web` (desktop site), `mobile` (app), `store` (physical retail).

## Data access

Questions about data access or personal data must go to data-governance@example.com.

with customers as (
    select * from {{ ref('stg_customers') }}
),
orders as (
    select * from {{ ref('orders') }}
)
select
    customers.customer_id,
    customers.first_name,
    customers.last_name,
    min(orders.order_date) as first_order,
    max(orders.order_date) as most_recent_order,
    count(orders.order_id) as number_of_orders,
    sum(orders.amount) as customer_lifetime_value
from customers
left join orders on customers.customer_id = orders.customer_id
group by customers.customer_id, customers.first_name, customers.last_name

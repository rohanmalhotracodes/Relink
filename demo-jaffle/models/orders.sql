with orders as (
    select * from {{ ref('stg_orders') }}
),
payments as (
    select * from {{ ref('stg_payments') }}
)
select
    orders.order_id,
    orders.customer_id,
    orders.order_date,
    orders.status,
    payments.amount
from orders
left join payments on orders.order_id = payments.order_id

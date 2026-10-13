# customer_name in conversations

The conversation list and the conversation detail both return `customer_name`, the display name of the customer in that conversation. The web app uses it for the inbox card and the header of the open conversation.

- `other_participant_user_id` (and `created_by`) stay as plain ids. They are for reference only.
- `customer_name` is the person's name, or `null` when we do not know it. It is never an email address. Show a neutral label such as "Customer" when it is null.
- Group conversations have no single customer, so `customer_name` is always `null` there.

## Which person is named

The backend names the customer-role participant of the conversation. If there isn't one, it names the other participant, which is the same id the provider list returns as `other_participant_user_id`. So the name always belongs to the person that id points to.

## Where the name comes from

In this order, first hit wins:

1. The caller's own token (if the caller is that person).
2. The identity service: the tenant's member list, then a single lookup by id.
3. The name the person's own sign-in gave us the last time they used chat (table `user_display_names`).
4. A name the person typed into this system: a training enrolment, a saved address, an order, a product or service review.

The identity service only lists people in the provider's tenant. A customer from outside it used to come back as `null`. Steps 3 and 4 cover that case.

## What this means in practice

- A customer is named for the provider after they have used chat once (opened their inbox, started or opened a conversation, sent or read a message). Nothing has to be backfilled.
- Someone who has never signed in to chat and has no enrolment, address, order or review still shows `null`. That is expected, the backend has no name to show.
- If the person changes their name, the saved name updates the next time they use chat.
- Names are cached in memory for a few minutes. The list still costs a fixed number of database queries however many conversations are on the page.

## Deploy

Run the migration once: `alembic upgrade b3e7c1a9d5f4` (creates `user_display_names`). If the table is missing, chat keeps working and `customer_name` falls back to steps 1, 2 and 4.

# Payment System

A small FastAPI payment backend currently using **Razorpay (test mode)** and **MongoDB Atlas**. It creates orders, starts payment attempts, verifies payments, and processes Razorpay webhooks.

> **Current status:** Razorpay integration is the implemented provider. Paytm has not been integrated yet. Keep using test credentials until the complete flow has been tested.

## 1. How the project is organized

```text
payment-system/
├── app/
│   ├── main.py
│   ├── database.py
│   ├── catalog.py
│   ├── orders.py
│   ├── payments.py
│   └── providers/
│       └── razorpay.py
├── .env                 # Local secrets; never commit this
├── .env.example         # Safe template; commit this
├── .gitignore
└── README.md
```

This is the expected layout based on the current project. Your project may also contain a frontend/checkout HTML file, tests, or other files not shown above.

### `app/main.py` — application entry point

- Creates the FastAPI application.
- Connects to MongoDB when the app starts and closes the connection at shutdown.
- Creates database indexes for order IDs and provider order IDs.
- Configures CORS (which browser origins are allowed to call the API).
- Registers the orders and payments routers.
- Exposes basic health/database test routes.

**Your friend may need to edit this file when:**
- Deploying the backend at a new domain: update the CORS allowed origins to the actual frontend origin.
- Adding a new router, such as a future Paytm-specific route.
- Changing database startup/index setup.

Do not add secret keys directly to this file.

### `app/database.py` — MongoDB connection

- Loads environment variables.
- Creates the asynchronous MongoDB client.
- Selects the database (defaults to `payment_system`).
- Provides startup and shutdown connection functions.

**Edit `.env`, not this file, to change the MongoDB connection string or database name.**

### `app/catalog.py` — products and prices

- Defines the products the API accepts.
- Stores product names and prices in **paise**. For example, `10000` means ₹100.00.
- The backend uses these prices to calculate the order total; it should not trust a price sent by the browser.

**Your friend edits this file** to add, remove, rename, or reprice products. Keep amounts as integer paise to avoid floating-point money calculations.

Example:
```python
PRODUCTS = {
    "example-item": {
        "name": "Example item",
        "amount_paise": 19900,  # ₹199.00
    },
}
```

### `app/orders.py` — order creation and lookup

- Validates the items and quantities in a new order.
- Looks up products and prices from `app/catalog.py`.
- Calculates the total on the server.
- Saves an order with a `pending` status in MongoDB.
- Provides an endpoint to retrieve an order by ID.

Main API routes:
- `POST /api/orders/` — create an order.
- `GET /api/orders/{order_id}` — retrieve an order.

### `app/payments.py` — payment lifecycle and webhook

- Starts Razorpay payment attempts for existing orders.
- Reuses an active attempt when the same order's payment-creation endpoint is called again.
- Verifies the Razorpay checkout signature and checks the payment details with Razorpay.
- Handles Razorpay webhook events, including captured and failed payments.
- Updates payment and order records together using MongoDB transactions.

Main API routes:
- `POST /api/payments/create/{order_id}` — prepare a Razorpay payment attempt.
- `POST /api/payments/verify` — verify checkout results.
- `POST /api/payments/webhook` — receive Razorpay webhook events.

Do not mark an order as paid based only on a browser redirect or frontend message. The backend must verify the provider's payment status.

### `app/providers/razorpay.py` — Razorpay API wrapper

- Creates Razorpay orders using the configured API credentials.
- Keeps provider-specific order creation code separate from the main payment routes.

### Frontend checkout file (if present)

The frontend usually:
1. Sends the cart items to `POST /api/orders/`.
2. Calls `POST /api/payments/create/{order_id}`.
3. Opens Razorpay Checkout.
4. Sends the checkout result to `POST /api/payments/verify`.

When the API is deployed at a new URL, update the API base URL in the frontend. Search the frontend HTML/JavaScript for the old `ngrok-free.app` URL or `127.0.0.1:8000`. Do not assume an ngrok URL is permanent.

## 2. Environment variables

Create a local `.env` file based on `.env.example`. Use real values only in `.env`.

Typical variables for the current setup:

```dotenv
MONGODB_URL=mongodb+srv://<username>:<password>@<cluster>/<database>?retryWrites=true&w=majority
MONGODB_DATABASE=payment_system

RAZORPAY_KEY_ID=your_razorpay_test_key_id
RAZORPAY_KEY_SECRET=your_razorpay_test_key_secret
RAZORPAY_WEBHOOK_SECRET=your_razorpay_webhook_secret
```

The exact MongoDB connection string comes from MongoDB Atlas. Razorpay test credentials and the webhook secret come from the Razorpay Dashboard. Never put real secrets in `README.md`, `.env.example`, screenshots, frontend JavaScript, or Git commits.

## 3. Run locally

From the project root, activate the virtual environment and start FastAPI:

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

Open the interactive API documentation at:

```text
http://127.0.0.1:8000/docs
```

If using a webhook from a provider outside your computer, configure the provider to send events to the publicly reachable URL ending in:

```text
/api/payments/webhook
```

For local testing, a temporary tunnel such as ngrok can provide a public URL. Its URL may change when restarted, so update the provider webhook URL and any relevant frontend/CORS configuration.

## 4. What the website owner should customize

Usually, your friend should start with these items:

1. **Products and prices:** edit `app/catalog.py`.
2. **Secrets and database connection:** create/update their local `.env` using `.env.example` as the template.
3. **Frontend API URL:** update the checkout HTML/JavaScript to point to the deployed backend.
4. **Allowed frontend origin:** update the CORS list in `app/main.py` for the real website domain.
5. **Payment provider setup:** configure the provider's credentials and webhook URL in its dashboard. Keep credentials on the backend only.
6. **Deployment configuration:** set environment variables in the hosting provider's secret/environment-variable settings; don't upload `.env` to public hosting or Git.

Before accepting real money, configure live credentials and production webhook settings carefully, test the complete flow, and review access control, logging, deployment, and operational recovery. This README is a project guide, not a production-readiness certification.

## 5. Could we migrate from MongoDB to Supabase?

Yes, but **Supabase is not a drop-in replacement for MongoDB**. Supabase primarily provides a hosted PostgreSQL relational database. The current backend uses MongoDB collections, MongoDB query syntax, indexes, and MongoDB transactions, so the database layer and related queries need to be rewritten.

A sensible migration plan:

1. **Create a Supabase project** and obtain its PostgreSQL connection details from the Supabase dashboard.
2. **Design relational tables**, for example:
   - `orders`: `order_id` (unique), `items` (often `jsonb`, or normalized into order-item rows), `amount_paise`, `currency`, `status`, timestamps, and payment-attempt tracking fields.
   - `payments`: `attempt_id` (unique), `order_id` (foreign key), `provider`, `provider_order_id`, `provider_payment_id`, `amount_paise`, `currency`, `status`, timestamps, and duplicate-capture tracking fields.
   - Add appropriate unique constraints, such as a unique order ID and a unique provider/provider-order-ID combination.
3. **Choose a PostgreSQL access library**, such as SQLAlchemy with its async PostgreSQL driver (`asyncpg`), or another async PostgreSQL client.
4. **Replace `app/database.py`** with a PostgreSQL connection/session setup.
5. **Rewrite database operations** in `app/orders.py`, `app/payments.py`, and `app/main.py`. MongoDB calls such as `find_one`, `insert_one`, `$set`, `$unset`, `$inc`, `create_index`, and `start_session()` are not PostgreSQL SQL and will not work unchanged.
6. **Preserve atomicity and retry safety.** Use PostgreSQL transactions, foreign keys, unique constraints, and conditional updates to keep order/payment state consistent. Keep provider API calls and secrets on the backend.
7. **Migrate existing data** only if needed: export MongoDB orders/payments, transform the documents into the new schema, import into Supabase, and verify record counts and statuses.
8. **Test before switching traffic:** order creation, duplicate payment attempts, successful/failed payments, webhook retries, and database failures. Keep a backup of the old database until the migration is verified.

Supabase also offers client libraries and a REST API, but for this existing FastAPI backend, a direct PostgreSQL server-side connection is often a natural fit. Do not expose a database service-role key in the browser. If using Supabase Auth or browser-side access, configure Row Level Security (RLS) policies appropriately.

**Important:** Changing only `MONGODB_URL` to a Supabase URL will not migrate this project. It requires schema design and code changes.

## Setting Up Your Own Razorpay Account (Live Payments)

This project currently uses Razorpay in Test Mode. To accept real customer payments, the website owner must connect their own Razorpay merchant account.

### 1. Create and activate a Razorpay account

1. Visit [Razorpay](https://razorpay.com/) and create an account using your own business and contact details.
2. Complete the account activation process and submit the required KYC, business, and bank account information.
3. Add your actual website details if requested by Razorpay.
4. Wait for Razorpay to approve and enable Live Mode for your account.

Test Mode is available for development, but real payments require the appropriate account activation and payment-method permissions.

### 2. Generate Live API keys

1. Sign in to the [Razorpay Dashboard](https://dashboard.razorpay.com/).
2. Switch from Test Mode to Live Mode.
3. Open **Account & Settings → API Keys** (the exact menu may vary).
4. Generate your Live API keys and securely save the Key ID and Key Secret.

Update your local `.env` file with your own credentials:

```dotenv
RAZORPAY_KEY_ID=rzp_live_your_key_id
RAZORPAY_KEY_SECRET=your_live_key_secret
RAZORPAY_WEBHOOK_SECRET=your_live_webhook_secret
```

Never commit `.env` to Git or expose the Key Secret in frontend JavaScript. Keep the existing test credentials available separately for development.

### 3. Configure the Razorpay webhook

1. In the Razorpay Dashboard, open the webhook settings.
2. Add your deployed backend URL, ending with:

   ` /api/payments/webhook`

3. Configure the events your backend handles, including `payment.captured` and `payment.failed`.
4. Set a webhook secret and place the same secret in `RAZORPAY_WEBHOOK_SECRET` on the backend.
5. Save the configuration and test that webhook events reach the deployed server.

Use the Live Mode webhook configuration for live payments. Do not use a temporary ngrok URL for a permanent production setup.

### 4. How real UPI payments work

The current backend creates a Razorpay payment order, and the frontend opens Razorpay Checkout.

Once Live Mode is correctly configured and UPI is enabled for the account:

1. The customer opens checkout on the website.
2. Razorpay Checkout presents the payment methods available for that account and transaction.
3. The customer selects an available UPI option and approves the payment using their UPI app.
4. The backend verifies the payment with Razorpay and processes the webhook.
5. The order is marked `paid` only after the backend confirms a successful captured payment.

The actual UPI options shown depend on Razorpay's current checkout support, account configuration, and eligibility. Test Mode does not represent a real money transfer.

### 5. How QR-code payments work

**Important:** The current project does not automatically generate a dedicated Razorpay UPI QR code merely because Live API keys are added.

There are two different flows:

- **QR option inside Checkout:** If Razorpay Checkout offers a QR payment option for the account and transaction, the customer can use that supported flow.
- **A dedicated QR code for your website:** This requires a separate QR-code implementation using Razorpay's supported QR Code APIs, or another suitable Razorpay product.

For a dedicated QR implementation, the backend would need to create the QR code using the Razorpay API, return the appropriate QR information to the frontend, and track the resulting payment. It must also verify payment status server-side and handle webhook retries safely. Do not mark an order paid merely because someone scanned a QR code.

See the [official Razorpay QR Code documentation](https://razorpay.com/docs/api/qr-codes/) before implementing this feature.

### 6. Before accepting real money

- Test the complete payment flow before launch.
- Confirm that Live Mode credentials are used only in the deployed backend.
- Confirm that successful captured payments update the correct order.
- Test failed payments, duplicate webhook deliveries, and retry behavior.
- Confirm that UPI and any QR features you intend to offer are enabled and available for your account.
- Keep API secrets out of Git and frontend code.

**Note:** Replacing the test credentials with Live Mode credentials enables the existing integration to use the live Razorpay environment, but it does not automatically implement a dedicated QR-code feature or guarantee that every payment method is enabled.

# Wallet money lives in a double-entry ledger enforced by Postgres

Wallets, event pools and uSync's own revenue are all ledger accounts. Every movement of money is a ledger transaction whose lines (signed cents per account) must sum to zero, and a stored balance per account is kept in step by a trigger. Lines and transactions can never be updated or deleted; corrections are new transactions. Postgres, not the Python services, enforces the zero sum, the balance updates, the immutability and the `balance >= 0` rule (except the external account and frozen wallets). We chose this over a single signed ledger table because money moving between a wallet and an event pool would otherwise be two unrelated writes that can silently disagree, and because one place answers both "what is every balance" and "where did every cent come from".

## Considered Options

- **One `wallet_ledger` table with signed amounts per wallet bucket**: fewest tables, but event pools and platform revenue sit outside it, so nothing guarantees money isn't created or lost in a transfer.
- **One row per movement (`from_account`, `to_account`)**: zero-sum by construction and easy to read, but a deposit with a fee or a payout to many players becomes several loosely grouped rows.
- **Full double-entry (chosen)**: four tables (`wallets`, `ledger_accounts`, `ledger_transactions`, `ledger_lines`) plus triggers; the most ceremony, and the clearest history and wallet state.

## Consequences

- Balances are never edited directly, including by uSync staff: a wired guaranteed prize or a support refund is its own transaction kind, written through an admin-only action and the audit log.
- Events, deposits and entries referenced by a ledger transaction can't be deleted (foreign keys restrict); events with money are cancelled, not deleted.
- Debugging money bugs means reading SQL triggers as well as Python.

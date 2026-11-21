# uSync

uSync is an esports platform where hosts run events and teams of players sign up and compete. This glossary covers the money side: wallets, entry fees and prizes.

## Language

### Wallet

**Wallet**:
The money an account holds on uSync. Every account has exactly one, shared by all of its profiles.
_Avoid_: Bank, balance, account

**Deposited Funds**:
The part of a wallet that came from deposits. It can be spent on entry fees but never withdrawn.
_Avoid_: Credit, deposit balance

**Earnings**:
The part of a wallet that came from prizes or host cuts. It can be spent on entry fees and is the only money that can be withdrawn.
_Avoid_: Winnings, profit, withdrawable balance

**Deposit**:
Money a user adds to their wallet by card through Stripe, at least $5.
_Avoid_: Top-up, load

**Deposit Fee**:
The 5% added on top of a deposit at checkout. It's uSync's, never part of the wallet.
_Avoid_: Processing fee, surcharge

**Manual Credit**:
Money uSync staff add to a wallet's deposited funds by hand, such as a wired guaranteed prize. A manual debit reverses one.
_Avoid_: Balance adjustment, top-up

**Withdrawal**:
Money paid out of a wallet's earnings to the user, outside uSync.
_Avoid_: Cash out, payout

**Ledger Entry**:
The dated record of one movement of money into or out of a wallet or an event pool. The record of every deposit, entry fee, payout and withdrawal.
_Avoid_: Transaction, payment, transfer

**Chargeback**:
A card deposit the user disputes with their bank and Stripe reverses.
_Avoid_: Dispute (that word belongs to match results)

**Frozen Wallet**:
A wallet that a chargeback left below zero. It can't pay entry fees or withdraw until it's back to zero or more.
_Avoid_: Locked, suspended

### Event money

**Entry Fee**:
The amount one player pays to play in an event, set by the host. A team's entry costs the entry fee times the event's team size.
_Avoid_: Buy-in, registration fee

**Pay for Team**:
The captain pays every picked player's entry fee from their own wallet.

**Each Pays Own**:
Every picked player's entry fee is taken from their own wallet the moment the captain signs the team up. Only possible when every picked player has auto-pay on.
_Avoid_: Split payment

**Auto-Pay**:
A player's opt-in, for a limited time, letting their team captains take entry fees from their wallet without asking.
_Avoid_: Payment request, pre-authorization

**Event Pool**:
The entry fees collected for one event, held until payout or refund. The platform fee comes out of it first, then the host cut; the rest is the prize pool.
_Avoid_: Pot, escrow

**Prize Pool**:
The money paid to the teams that place: what's left of the event pool after the platform fee and host cut, or, in a guaranteed event, the host's guaranteed prize.
_Avoid_: Prize, winnings

**Host Cut**:
The part of the event pool that goes to the host after the platform fee: a percentage the host sets, or all of it in a guaranteed event.
_Avoid_: Host percentage, rake

**Platform Fee**:
uSync's 5% of an event pool, taken when the event ends, before the host cut. Never taken from a guaranteed prize.
_Avoid_: Rake, commission

**Guaranteed Event**:
An event where the host pays the prize pool up front, when sign-ups open, and keeps the event pool after the platform fee.

**Entry-Funded Event**:
An event whose prize pool is what's left of the event pool after the platform fee and host cut.
_Avoid_: Non-guaranteed event, fee-funded event

**Payout Structure**:
How the host splits the prize pool across placements, as percentages adding up to 100.
_Avoid_: Prize split, payout table

**Payout**:
Paying an event pool out to the host and the placing players when the event ends. A placing team's share is split equally among the players who played for it.
_Avoid_: Withdrawal, distribution

**Cancelled Event**:
An event with money that the host, or uSync for too few entrants, called off; every entry fee and guarantee goes back to whoever paid it. It's kept, never deleted.
_Avoid_: Deleted event

### Results

**Standings**:
The final placement of every entrant in an event, recorded when the event completes.
_Avoid_: Results, leaderboard, rankings

**Placement**:
One entrant's finishing position in an event's standings. Entrants can share a placement.
_Avoid_: Rank, place, position

**Stalled Match**:
A match whose sides are both known but that has had no report from either side for too long, so it goes to the admin queue.
_Avoid_: Stuck match, abandoned match

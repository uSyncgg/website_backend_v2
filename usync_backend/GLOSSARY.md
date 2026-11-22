# uSync

uSync is an esports platform where hosts run events and teams of players sign up and compete. This glossary covers the money side (wallets, entry fees and prizes), match results and chat.

## Language

### Events

**Hosted Event**:
A league, LAN or tournament a host runs on uSync, with its sign-ups and bracket here. It's listed on its game's pages from the moment it's created until it completes or is cancelled.
_Avoid_: Bracket event, posted event, user event

**Listed Event**:
A league, LAN or tournament run somewhere else that uSync lists and links out to, either scraped or submitted and approved by uSync staff.
_Avoid_: External event, directory event, verified event

**Game Mode**:
How matches in a hosted event are played, such as Hardpoint or Search and Destroy. An event has exactly one, and every map of every match uses it.
_Avoid_: Gamemode, playlist, match type

**Ruleset**:
An official set of competition rules, published by an outside organisation such as the CDL, that a host picks for a hosted event. It applies to the event exactly as published: hosts can't add to it or change it.
_Avoid_: Rules, custom rules, house rules

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
Money paid out of a wallet's earnings to the user, outside uSync. The user asks for an amount of their earnings, or all of them, and uSync staff send it. A wallet has at most one pending withdrawal at a time.
_Avoid_: Cash out, payout

**Pending Withdrawal**:
Earnings a user has asked to withdraw that uSync hasn't sent or returned yet. They can't be spent, moved or cancelled by the user.
_Avoid_: Withdrawal pool, held funds

**Withdrawal Completed**:
A withdrawal uSync staff have sent to the user. The money has left the wallet for good.
_Avoid_: Withdrawal approved, paid

**Withdrawal Returned**:
A withdrawal uSync staff didn't send, with a reason. The money goes back to the wallet's earnings.
_Avoid_: Withdrawal failed, rejected, declined

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

**Paid-Out Event**:
An event with money whose payout has been made. Its results and money can no longer change, and it can't be cancelled.
_Avoid_: Closed event, settled event

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

**Dispute Window**:
The time after a match's result during which a side can still dispute it. It ends 5 minutes after the result, when the next match gets a report, or when a host or admin rules on the match, whichever comes first.
_Avoid_: Dispute period, appeal window

**Stalled Match**:
A match whose sides are both known but that has had no report from either side for too long, so it goes to the admin queue.
_Avoid_: Stuck match, abandoned match

### Chat

**Chat**:
A conversation between a set of members on uSync. Every chat is of one kind, such as a match chat.
_Avoid_: Room, channel, thread

**Match Chat**:
The chat for one match between the picked players of both sides, which the event's host and admins can join. It opens once both sides are known and closes for good after the chat grace period.
_Avoid_: Match room, lobby chat

**Chat Member**:
A profile that can read and write in a chat: a picked player from the start, or an event admin once they join.
_Avoid_: Participant, user

**Chat Message**:
One message in a chat, written by a member or posted by uSync itself. It can never be edited or deleted.
_Avoid_: Post, comment

**Chat Grace Period**:
The 15 minutes a match chat stays open after its dispute window ends and any dispute or admin request is settled.
_Avoid_: Cooldown

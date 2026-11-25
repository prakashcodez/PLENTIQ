# Plentiq deferred features

These are future milestones. They are recorded here and are not part of the current implementation.

## One-time pickup verification PIN

- After approval, create a one-time numeric pickup PIN that only the acceptor can see.
- The donor enters the PIN at handover; only a successful match may confirm collection.
- Before implementation, add attempt limits and decide how to store a verifier safely. A four-digit value has a small search space, so storing it as plain text is not appropriate.
- Likely database change: a verifier and expiry/attempt metadata on the existing collection record.

## Urgent expiry prioritization

- Mark active listings with less than two hours before the pickup deadline as urgent.
- Sort urgent listings before other food in the acceptor catalog, then by earliest deadline.
- This should use the existing pickup deadline and needs no schema change.

## Printable completed-donation summary

- Add a branded HTML page with browser print styling for completed collections.
- Include listing, donor, acceptor, collection timestamp, and quantity with its unit.
- Call it a donation/collection summary, not a tax receipt. The app does not record verified tax value, food safety certification, or distribution totals.
- This can use native browser printing and does not require a PDF package.

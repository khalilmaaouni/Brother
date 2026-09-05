AD-01: same postal code, only difference is full width versus half width digit notation, no relation or difference stated.
AD-02: same lot number in kanji versus hyphen notation, building name and room explicitly not recorded on both sides.
AD-03: same town name and lot number but the city or ward differs (Yokohama Naka ku versus Kawasaki Kawasaki ku).
AD-04: same building same lot number but floors 3 and 8 are stated to hold different tenants (different occupying businesses).
AD-05: pre and post municipal merger address for the same stated chiban lot number, only the town name and postal code changed.
AD-06: single record, registered address (Fukuoka) conflicts with its own postal code (Sapporo), unexplained conflict.
AD-07: identical address, only difference is the kana variant of one character (ke versus small ke).
AD-08: prefecture field is blank on one side, remaining address matches but Fuchu city exists in both Tokyo and Hiroshima.
AD-09: shipping address field only says head office, prefecture, city, lot number and postal code all blank.
AD-10: identical shared office floor, but two different corporate numbers are stated for the two candidates.
EO-01: same corporate number for HQ and branch records, branch explicitly holds its own credit limit and billing destination.
EO-02: sold to and payer role pair, but payer is named as the parent holding company with its own payment terms.
EO-03: same day duplicate from one purchasing department, all fields match, explicitly zero order or billing history on either.
EO-04: same company name but two different corporate numbers, explicitly no capital relationship between them.
EO-05: same corporate number, customer role and supplier role pair for the same external counterparty, same contact window.
EO-06: same site, but one master is stated to be a one to many consolidation hub covering five nearby stores.
EO-07: same name and address, one side's corporate number is blank, both sides carry a year of order history, different sales reps.
EO-08: request carries only a bare name, address, corporate number, contact and phone all stated blank, three existing candidates.
EO-09: parent and 100 percent owned subsidiary, different corporate numbers, same building and floor.
EO-10: same corporate number, role and billing destination, one created during ERP migration, only notation width differs, both have orders.
HI-01: capital, trade flow and reporting parents are all different named entities, and the master has only one parent field.
HI-02: request to flip an existing capital parent child link based only on a sales performance change, no new capital fact stated.
HI-03: business transfer of 3 stores to a new parent, with pre transfer sales explicitly aggregated under the old parent.
HI-04: territory reassignment (sales rep move) is being framed as a request to change the capital hierarchy parent.
HI-05: franchisee shares brand and signage but is stated to have no capital relationship or shared directors.
HI-06: newly formed subsidiary with a similar name to the existing holding company, flagged as a match candidate by name matching.
HI-07: input states only vague group membership, explicitly not saying whether it is capital, trade flow or reporting.
HI-08: two group companies each keep their own company code record for the same external counterparty, terms differ by code.
HI-09: a location node and an org node share a name but their jurisdictions only partially overlap.
HI-10: request to set a trade flow distributor as the stores' capital corporate parent, conflating two hierarchy types.
ID-01: same 13 digit numeric value but one side is a self assigned internal sequence and the other a corporate number.
ID-02: qualified invoice number expired 2026-06-30, today is 2026-09-05, no new number obtained yet.
ID-03: two legacy IDs cross walked in a migration table, corporate number and customer role also match independently.
ID-04: store ID and site ID share the same 6 digit scheme but the input states they point to different targets.
ID-05: same customer code, explicitly reused for a different legal entity from 2023-10-01 onward.
MM-01: three stores share one corporate number but each has its own delivery destination, shelf plan and sales booking.
MM-02: 13 digit value matches, but one side is an individual's invoice number and the other a corporate number.
MM-03: phone number and building name match only because both are unrelated tenants sharing the landlord's line, different corporate numbers.
MM-04: same corporate number, role, site and billing destination, one record explicitly has zero history and a duplicate registration comment.
MM-05: match rests on a 0.83 score alone, both corporate numbers blank, and the merge implementation is stated to be irreversible.
MM-06: same reading but different kanji spelling, different banchi, both corporate numbers explicitly not yet obtained.
MM-07: different trade names but same corporate number, with a registered 2025-04-01 name change on file explaining it.
MM-08: candidate belongs to a different operating company's tenant, contractually barred from cross reference.
MM-09: one side's attributes are inaccessible by permission control, only a 0.91 match score is visible.
MM-10: same legal entity's two billing records differ only in payment terms tied to two different contract types.
RQ-01: governance minutes record an explicit approved 0.95 threshold decision, matches the extracted rule exactly, DECIDED.
RQ-02: ticket states payment terms are managed in ERP with the contract, a reasonable derivation rather than a formal decision, INFERRED.
RQ-03: requirement only covers same corporate number records and is explicitly silent on store records, extending it is a gap filling guess, ASSUMED.
RQ-04: BRD explicitly defers the normalization rule to a future decision with no record made, so the extracted rule has no basis, UNKNOWN.
RQ-05: a dated decision record with a named approver fixes the source as ERP, matching the extracted rule exactly, DECIDED.
SV-01: formal legal name question, corporate registry is the authoritative source for a company's registered legal name.
SV-02: qualified invoice registration number question, the tax authority is the issuing and authoritative source for that number.
SV-03: payment terms question, the underlying contract document is stated to be attached in ERP, so ERP is the source.
SV-04: sales rep question, CRM holds the current person and its update is newer than ERP's stale prior holder.
SV-05: display name question, a manual override set 2026-08-01 is still valid through 2026-12-31 as of today, so it wins.
SV-06: legal name question, the manual override expired 2026-03-31, so it falls back to the registry, which reflects the 2026-04-01 change.
SV-07: golden ID question, MDM owns the golden identifier by definition, CRM's number is only its own internal management number.
SV-08: legal name question, ERP's update carries an old 2026-02-10 source timestamp, older than the registry's already applied 2026-04-01 change.
SV-09: display name question, CRM's blank is stated to mean not yet entered rather than a deletion, so the populated sales master value stands.
SV-10: billing address question, neither record carries a source type, updater or timestamp to break the tie, NO-DATA.
TM-01: February 2024 falls inside R1's 2019-04-01 to 2024-03-31 validity window, before R2 even opens, R1.
TM-02: 2025-02-10 falls inside R1's window ending 2025-03-31, before R2 starts 2025-04-01, R1.
TM-03: today 2026-09-05 falls after R2's 2026-01-15 start and after R1 already ended 2026-01-14, R2.
TM-04: the unmerge on 2026-06-10 restored R1 and R2 and invalidated R3, and the transaction was originally on R2, so R2.
TM-05: today 2026-09-05 is before R2's future dated 2026-12-01 start, so R1 is still the currently effective record.
TM-06: neither record carries any valid_from, valid_to, source_timestamp or created_at to establish order, NO-DATA.
TM-07: the 2023-05-20 invoice predates the 2023-10-01 code reuse, so it belongs to the original holder R1.
TM-08: 2026-08-01 falls after R1's superseded end 2026-06-30 and after R2's 2026-07-01 start, R2.
TM-09: 2026-03-15 falls in the gap after R1's 2025-12-31 suspension and before R2's 2026-04-01 reopening, neither covers it, NO-DATA.
TM-10: the reissued invoice is for August 2025, which falls inside R1's window ending 2025-11-30, so the address on record then applies, R1.

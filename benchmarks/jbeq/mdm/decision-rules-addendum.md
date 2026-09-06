# JBEQ-MDM decision rules addendum

This file is what the rules arm receives beside the vocabulary block in
`benchmarks/jbeq/README.md` and `scripts/jbeq_mdm.py` (the `VOCABULARY`
constant, boundary rules 1 to 4). It does not replace that block and it is
never copied into a prompt file: the vocabulary stays the one thing every
prompt carries verbatim, and this addendum is separate context the rules arm
is handed alongside it, the same way it was handed the steering directive's
sections before this file existed.

Nothing here names a benchmark case id, a benchmark phrase, or a benchmark
answer set. Each rule is a general boundary rule in the same numbered,
bilingual style as boundary rules 1 to 4, continuing that numbering, and each
one is written so it decides every case of its class, not one case.

## Why this file exists

Two blind rounds against the JBEQ-MDM seed left the rules arm making the same
three kinds of mistake, one of which recurred across both rounds. Reading the
rationale behind each mistake showed the vocabulary's existing boundary rules
were the right rules in the abstract but under-specified at the point where a
reasoning surface actually has to decide: what counts as a stated relation,
what counts as a refuting fact, and whether an operational split cancels a
recorded relation. Rules 5 to 7 below close exactly those three gaps.

A third blind round then made a fourth kind of mistake: it read rules 5 and 7's
"an operational reason does not erase the relation" language as covering cases
where the input actually states a fact that refutes identity, collapsing a
distinction rule 6 already drew. Reading that round's misses whole surfaced
four more general gaps beside the collision itself: where the ESCALATE and
NO-DATA boundary stalls on a single blank field even when another fact
corroborates, where the merge boundary lets history or irreversibility alone
license a merge with no evidence floor beneath it, where a numeric identifier
match needs a domain and tenant check before it can support anything, and
where a record legitimately has more than one valid parent because parent
hierarchies are not all the same hierarchy. Rules 8 to 11 close those four
gaps, and the note right after rule 7 closes the collision.

## 決定境界の追加 (boundary rules 5 to 11)

5. LINK AS RELATED か KEEP SEPARATE か(所在地の共有のみの場合)。
   同一の敷地または同一の所在地であるという事実は、それだけでは境界規則1の
   いう「関係」ではない。入力が法人番号の一致、親子関係、役割の対、商流上の
   経由のいずれも述べておらず、共有されている事実が所在地のみであり、かつ
   一方のレコードが他方に対して一対多の機能(複数の店舗や拠点をまとめて受け
   持つ共同配送拠点、共有サービス拠点、集約センターなど)を果たしている場合
   は、KEEP SEPARATE と答える。所在地が同じであることを関係の根拠に読み替え
   てはならない。一対多の機能を統合すると、他の拠点分の記録が誤った一件に
   紐づく。
   EN: Same site or same address, on its own, is not the relation boundary
   rule 1 asks for. When the input states no corporate number match, no
   parent-child link, no role pair, and no trade-flow pass-through, and the
   only fact the two records share is their address, and one record performs
   a one-to-many function relative to the other (a shared delivery hub, a
   shared-service site, or a consolidation point covering several stores or
   locations), answer KEEP SEPARATE. Do not read a shared address as if it
   were a stated relation: merging or linking a one-to-many object into a
   single-site record misattributes every other site's records to the one
   that absorbed it.
   この規則は所在地の一致度合いより先に判定する(round 10 repair,
   2026-09-06, review-u3-2026-09-06.md W-04)。所在地が表記違いに過ぎない
   一致(notation_variant_only)であっても、一対多の対象を吸収してよい理由
   にはならない: 統合の安全確認より先に規則5が答える。
   EN: This rule is decided before how closely the two addresses match
   (round 10 repair, 2026-09-06, review-u3-2026-09-06.md W-04). Even when
   the address match is merely a notation variant (notation_variant_only),
   that closeness is never a reason to absorb a one-to-many object: rule 5
   answers before the merge-safety checks that a close address match would
   otherwise trigger.
   規則5の原則は一対多の場合だけに限らない(round 11 repair, 2026-09-06,
   review-u4-2026-09-06.md W4-23, engine mutation id
   same_site_only_never_a_relation)。stated_relation が same_site_only
   (所在地の共有のみ)である場合、それは境界規則1が問う「関係」そのもの
   ではない。rule_r の判定と境界規則1の既定判定は、stated_relation が
   none の場合と同じ扱いを same_site_only にも与え、所在地の共有のみを
   もって LINK AS RELATED へ落ちることを防ぐ。
   EN, round 11 (2026-09-06, review-u4-2026-09-06.md W4-23, engine mutation
   id same_site_only_never_a_relation): rule 5's principle is not limited
   to the one-to-many case. Wherever the engine asks "is there a stated
   relation at all" (rule_r's own stated_relation=="none" guard, and
   boundary rule 1's default), same_site_only reads exactly like "none":
   a shared address alone is never the stated relation, so it must not
   fall through to LINK AS RELATED on its own.

6. KEEP SEPARATE か REJECT MATCH か(読みの一致または表記の近さのみの場合)。
   商号の読み(ヨミ)が一致していても、表記(漢字)が異なることは、それだけで
   は境界規則2のいう同一性を否定する事実ではない。番地の相違、部署名の相違、
   法人番号の欠落も、単独では否定する事実ではなく、単なる裏付けの欠如であ
   る。REJECT MATCH は、入力が別の実体であることを明言する事実(別のテナン
   トである、別法人へ譲渡済みである、法人番号が異なる、契約上別扱いと明記
   されているなど)を伴う場合に限って答える。そのような明言を欠くときは、
   裏付けの弱さを根拠に REJECT MATCH へ格上げせず、KEEP SEPARATE と答える。
   EN: A shared reading with a different kanji spelling of a company name is
   not, by itself, a fact that refutes identity under boundary rule 2. A
   differing address number, a differing department name, or a missing
   corporate number are, on their own, not refuting facts either: they are an
   absence of support. Reserve REJECT MATCH for input that states outright
   that the two are different entities (a stated different tenant, a stated
   transfer to another legal entity, differing corporate numbers, a stated
   contractual separation, and the like). Without such a stated fact, do not
   upgrade weak support into REJECT MATCH; answer KEEP SEPARATE.

7. LINK AS RELATED か KEEP SEPARATE か(グループ内で会社コード別に保持され
   る記録の場合)。同一グループ内の複数の事業会社や法人が、同一の外部の取引
   先や仕入先について、それぞれ自社の会社コードで個別の記録(与信枠、価格条
   件、支払条件など)を保持している場合、運用上その記録は統合してはならない
   が、両方が同一の外部実体を指しているという事実そのものが境界規則1のいう
   関係である。KEEP SEPARATE ではなく LINK AS RELATED と答える。会社コード
   ごとに条件を分けて管理する運用上の理由は、関係を消す理由にはならない。
   EN: When two or more operating companies or legal entities in the same
   group each hold their own company-code-specific record (credit limit,
   pricing terms, payment terms, and the like) for the same external
   counterparty or supplier, the records must not be merged operationally,
   but the fact that both records name the same external entity is itself the
   relation boundary rule 1 asks for. Answer LINK AS RELATED, not KEEP
   SEPARATE. An operational reason to keep the records apart, such as
   company-code-specific credit or pricing management, is never a reason to
   record no relation at all.

規則5、6、7を混同しないために。規則5と規則7は同じ軸、LINK AS RELATED か
KEEP SEPARATE かを判定する。規則5は関係を否定する向き(所在地の共有だけでは
関係にならない)、規則7は関係を維持する向き(会社コード別の運用管理は既に
述べられた関係を消さない)で、互いに逆向きだが同じ軸の上にある。規則6は
別の軸、KEEP SEPARATE か REJECT MATCH かを判定し、REJECT MATCH と答えるのは
入力が別の実体であることを明言する事実(別のテナント、別法人への譲渡、
異なる法人番号、契約上の別扱いなど)を伴う場合に限る。規則5、7の「運用上の
理由は関係を消さない」という文言を、そのような明言された否定事実がある
場合にまで広げてはならない。否定する事実が明言されているときは規則6(そし
て規則10)が優先し、REJECT MATCH と答える。
EN, to avoid the collision that made rules 5, 6 and 7 hard to tell apart in
one read: rules 5 and 7 sit on the same axis, LINK AS RELATED versus KEEP
SEPARATE. Rule 5 runs in the direction of denying a relation (a shared
address alone is not one); rule 7 runs in the direction of preserving one (an
operational reason, such as separate company-code management, does not erase
a relation already stated). They point opposite ways on the same axis. Rule 6
sits on a different axis, KEEP SEPARATE versus REJECT MATCH, and answers
REJECT MATCH only when the input states outright that the two are different
entities (a stated different tenant, a stated transfer to another legal
entity, differing corporate numbers, a stated contractual separation, and the
like). Never stretch rule 5 or 7's "an operational reason does not erase the
relation" to cover a case where such a stated refuting fact is present: when a
fact says the two are not the same, rule 6 (and rule 10 below) governs, and
the answer is REJECT MATCH.

8. ESCALATE か NO-DATA か(一項目が空欄でも他の事実が裏付ける場合)。
   ある項目(例えば都道府県欄)が空欄であっても、番地、郵便番号、その他の
   事実が一致し、確定はしないまでも裏付けとなる場合、判断材料が無いわけで
   はない。この場合は NO-DATA ではなく ESCALATE と答える。NO-DATA は、その
   欄の空欄を裏付ける事実が入力のどこにも無い場合に限って答える。
   EN: Even when one field (the prefecture, for example) is blank, if another
   stated fact (a building number, a postal code, and the like) corroborates
   without fully confirming, that is something to judge, not nothing to
   judge: answer ESCALATE, not NO-DATA. Reserve NO-DATA for when nothing in
   the input corroborates the blank field at all.

9. AUTO-MERGE か SUGGEST MERGE か ESCALATE か(統合の判断順序、証拠の強さを
   先に見る規則)。統合してよいかは、まず証拠の強さを判定し、そのあとに確認
   理由(取引履歴、処理の不可逆性、未検証の移行対応表)を見る。矛盾の無い
   識別子の一致は強い証拠、説明のつく矛盾を伴う識別子や未検証の移行対応表
   は中程度の証拠、名寄せスコアのみまたは識別子の欠落は弱い証拠である。強い
   証拠で確認理由が無ければ AUTO-MERGE。中程度以上の証拠に確認理由が加われ
   ば SUGGEST MERGE。証拠が弱い場合は確認理由の有無にかかわらず ESCALATE と
   し、処理が不可逆であることは弱い証拠を統合の根拠に格上げしない。
   EN: Decide whether to merge by evidence strength first, then by the
   confirmation reason (transaction history, an irreversible step, an
   unvalidated migration crosswalk). An unexplained, conflict-free identifier
   match is strong evidence; an explained conflicting identifier or an
   unvalidated migration crosswalk is medium evidence; a match score alone or
   a missing identifier is weak evidence. Strong evidence with no
   confirmation reason: AUTO-MERGE. Medium or strong evidence plus a
   confirmation reason: SUGGEST MERGE. Weak evidence: ESCALATE regardless of
   the confirmation reason; irreversibility never upgrades weak evidence into
   a merge.
   中程度の証拠であっても、確認理由(取引履歴または処理の不可逆性)がその
   証拠自体とは別に述べられていなければ SUGGEST MERGE ではなく ESCALATE と
   する(round 10 repair, 2026-09-06, review-u3-2026-09-06.md W-19)。未検証
   の移行対応表は、それ自体が中程度の証拠であり、同時に自らの確認理由として
   二重に数えてはならない。
   EN: Medium evidence with no such separately stated confirmation reason
   anywhere in the input gives ESCALATE, not SUGGEST MERGE (round 10 repair,
   2026-09-06, review-u3-2026-09-06.md W-19). An unvalidated migration
   crosswalk must never be counted twice, once as the medium evidence and
   again as its own confirmation reason.

   証拠が中程度で、かつその証拠自体とは別に確認理由(取引履歴、処理の不可
   逆性など)が入力に述べられている場合は SUGGEST MERGE。証拠が中程度で
   あっても、その証拠とは別の確認理由がどこにも述べられていない場合は
   ESCALATE と答える。未検証の移行対応表そのものを、証拠であると同時に
   その確認理由として二重に数えてはならない。
   EN: Medium evidence together with a confirmation reason stated
   separately from that evidence itself (transaction history, an
   irreversible step, and the like) gives SUGGEST MERGE. Medium evidence
   with no such separately stated confirmation reason anywhere in the
   input gives ESCALATE. An unvalidated migration crosswalk must never be
   counted twice, once as the medium evidence and again as its own
   confirmation reason.

10. KEEP SEPARATE か REJECT MATCH か(税務・法人番号の体系不一致、または
    テナント境界の場合)。数字そのものが一致していても、入力がその値を別の
    識別子体系または別の法人格の種別から来たものと明記している場合(例えば
    自社の社内連番と法人番号、個人の適格請求書発行事業者番号と法人の法人
    番号)、その一致自体が無効であり REJECT MATCH と答える。同様に、候補が
    明記されたテナント境界または契約上の区分を越える場合も REJECT MATCH と
    する。これは規則6、7が扱う、運用上の理由で記録を分けて保持する場合とは
    異なる。規則10は、入力が「同一ではない」または「参照してはならない」と
    明言している場合に適用する。
    EN: A numeric match invalidates itself when the input states the values
    come from different identifier domains or different legal person types
    (an internal sequence number versus a corporate number, an individual's
    qualified invoice registration number versus a corporate number), and the
    same holds when a candidate crosses a stated tenant boundary or
    contractual separation: answer REJECT MATCH. This is distinct from rules
    6 and 7, which govern records kept apart or linked for an operational
    reason; rule 10 applies when the input states outright that the two are
    not the same, or must never reference each other.

    round 10 追加 (2026-09-06, engine rule C, mutation id
    rule_c_hierarchy_dimension): 同名の階層ノード同士であっても、入力が
    両者は別の階層次元に属すると明記している場合(例えば地理的地域の階層と
    販売組織の階層)、名称が一致しているという事実は同一性を裏付けない。
    この場合も規則10と同じく REJECT MATCH と答える。階層次元が異なる二つの
    ノードは、名称がどうであれ同じノードではない。
    EN, round 10 addition (2026-09-06, engine rule C, mutation id
    rule_c_hierarchy_dimension): the same reject-match logic covers two
    hierarchy nodes that share a name when the input states outright that
    they belong to different hierarchy dimensions (a geographic area
    hierarchy versus a sales organisation hierarchy, for instance). A
    shared name never refutes that distinction: two nodes from different
    hierarchy dimensions are not the same node whatever they are called.
    Answer REJECT MATCH, exactly as rule 10 already does for a stated
    different identifier domain or tenant boundary.

11. LINK AS RELATED か ESCALATE か(資本、商流、レポーティングなど階層の種類
    が異なる複数の親を持つ場合)。資本上の親、商流上の親、レポーティング上
    の親は互いに独立した階層である。入力がそれぞれの階層について別の有効な
    親を述べている場合、それは一つに決着すべき矛盾ではなく、識別ではなく
    関係を裏付ける証拠である。一つの「親」欄にどれか一つだけを選ばせるのは
    システムの実装上の制約であって、記録すべき関係を消す根拠にはならない。
    この場合は LINK AS RELATED と答え、業務側の判断を要する本当の矛盾がある
    場合に限り ESCALATE を残す(founder ruling 2026-09-06,
    decision-p0-3-rule-d-hi01: 識別ではなく関係を裏付ける証拠は LINK AS
    RELATED であり、KEEP SEPARATE は記録すべき関係が無いことを意味する)。
    EN: Capital, trade-flow and reporting parents are independent hierarchy
    types. When the input states a different valid parent for each type,
    that is not one conflict needing a single resolution: it is evidence
    that supports a relationship, not identity. A single master-parent
    field that forces one choice is an implementation limit, never a reason
    to erase a relationship that should be recorded. Answer LINK AS
    RELATED; reserve ESCALATE for an actual conflict that needs a business
    decision. (Founder ruling 2026-09-06, decision-p0-3-rule-d-hi01-2026-09-06:
    evidence that supports a relationship without supporting identity is
    LINK AS RELATED, and KEEP SEPARATE means no relationship to record; see
    docs/decisions/decision-p0-3-rule-d-hi01-2026-09-06.json.)

12. 移転(lifecycle=relocated)における所在地の相違は、境界規則2のいう同一性
    を否定する事実ではない(round 10 repair, 2026-09-06,
    review-u3-2026-09-06.md W-01)。移転そのものが所在地の相違を生じさせる
    のであって、相違が移転を否定するのではない。したがって lifecycle が
    relocated であるレコードについては、location_comparison=
    different_administrative_area だけを理由に REJECT MATCH と答えては
    ならず、証拠の強さと確認理由に関する規則9の判定へ進む。これはあくまで
    抽出漏れに対する後備の規則であり、本来の修正は location_comparison の
    語彙そのもの(同一行政区域内の二つの実在する住所は
    different_administrative_area ではなく null とすること)にある。
    EN: A location difference under lifecycle=relocated is not, by itself,
    a fact that refutes identity under boundary rule 2 (round 10 repair,
    2026-09-06, review-u3-2026-09-06.md W-01). The relocation is what
    produces the address difference; the difference does not refute the
    relocation. So a record with lifecycle=relocated must not be answered
    REJECT MATCH on location_comparison=different_administrative_area
    alone: it proceeds to rule 9's evidence-strength and confirmation-reason
    test instead. This is a backstop for an extraction gap; the primary fix
    is in the location_comparison vocabulary itself (two real addresses
    inside one administrative area read null, not
    different_administrative_area).


13. LINK AS RELATED か REJECT MATCH か(記録の書き込みが提案されている場合)。
    stated_relation が none 以外の値を述べていても、それは「関係が何である
    か」を答えるものであって、一方のコードを他方に上書きして書き込んでよい
    という許可ではない。requested_action=record_write(書き込みの提案)の
    場合に限り、入力自身が述べる否定する事実(規則2の different_legal_
    entity、site_store の distinct_operational_attributes)は、それと同時に
    述べられている関係よりも優先する。否定する事実が書き込みの提案そのもの
    を否定しているのであって、単に関係を述べているのではないためである。
    この場合は LINK AS RELATED ではなく REJECT MATCH と答える(提案が無い
    場合は KEEP SEPARATE。boundary rule "proposal_gate" と同じ判定)。
    requested_action が none、assignment、match_on_stated_basis である場合
    は、この規則の対象外であり、規則2および site_store は変更前と同じ答え
    を返す。
    EN: LINK AS RELATED versus REJECT MATCH, when the input proposes a
    WRITE. A stated relation (stated_relation != "none") answers "what is
    the relation", it is never a license to write one code over the other.
    Only when requested_action=record_write (a proposal to write) does a
    stated refuting fact (rule 2's different_legal_entity, site_store's
    distinct_operational_attributes) outrank a relation stated alongside
    it: the refuting fact refutes the write proposal itself, not merely a
    relation. Answer REJECT MATCH, not LINK AS RELATED (or KEEP SEPARATE
    when there is no proposal at all, the same boundary the proposal_gate
    id already applies elsewhere). requested_action values none,
    assignment and match_on_stated_basis are unaffected: rule 2 and
    site_store return the same answer they always did for those. (Round
    11, 2026-09-06, review-u4-2026-09-06.md W4-03 and W4-04; engine
    mutation ids site_store_write_refutes and
    link_vs_reject_write_refutes. One principle: a refuting fact beats a
    stated relation when the proposal is a write, and a shared address is
    never the relation.)

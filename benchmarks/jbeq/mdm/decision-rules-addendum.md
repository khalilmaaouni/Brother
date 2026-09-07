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

   規則6の補遺(2026-09-07 repair, review-rule6-2026-09-07.md ranking item
   2、U3 W-03 を解消)。同一の町および街区で、地番のみが異なる二つの住所
   は、規則6のいう「裏付けの欠如」の一例であり、それだけでは同一性を否定
   する事実ではない。fact-sheet-schema.json の location_comparison に
   same_area_different_lot という値を追加し、scripts/jbeq_decide.py の
   ルールLはこの値について KEEP SEPARATE と答える(REJECT MATCH でも統合
   でもない)。この値が存在しなかった間、抽出者には書くべき正しい値が無く、
   different_administrative_area と誤って書かれ、規則6が禁じる「裏付けの
   弱さの格上げ」が contradicted_attributes 経由ではなく location_comparison
   経由のルールLで起きていた。
   EN, rule 6 addendum (2026-09-07 repair, review-rule6-2026-09-07.md
   ranking item 2, closes U3 W-03): two addresses that state the same town
   and block and differ only in lot number are exactly the kind of absence
   of support rule 6 describes, never a refutation on their own.
   fact-sheet-schema.json's location_comparison vocabulary gained a value
   for this, same_area_different_lot, and scripts/jbeq_decide.py's rule L
   answers KEEP SEPARATE for it, never REJECT MATCH and never a merge.
   Before this value existed the extractor had no correct value to write
   and wrote different_administrative_area instead, so rule 6's forbidden
   upgrade (weak support into REJECT MATCH) was happening through
   location_comparison and rule L, not through contradicted_attributes.

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

13. 規則L、地名の改称(location_comparison=same_area_renamed)の場合。市町村
    合併や町名変更のみで地番など他の事実が一致している場合、それは地図上の
    事実にすぎず、レジストリの二行が一つであることの確認ではない。したがっ
    て単独では AUTO-MERGE を根拠づけず、SUGGEST MERGE と答えて人による確認
    を求める(founder ruling 2026-09-06,
    decision-p0-3-same-area-renamed-2026-09-06、mutation id
    renamed_area_needs_a_person)。表記のみの相違である
    notation_variant_only は、この規則の対象ではなく、従来どおり
    AUTO-MERGE-gate-checked の経路のまま変わらない。
    EN: Rule L, a renamed area (location_comparison=same_area_renamed). A
    municipal merger or a town/street renaming alone, with other facts
    such as the lot number stated identical, is a fact about the map, not
    confirmation that the two registry rows are one. So it does not by
    itself justify AUTO-MERGE: answer SUGGEST MERGE and ask a person to
    confirm (founder ruling 2026-09-06,
    decision-p0-3-same-area-renamed-2026-09-06, mutation id
    renamed_area_needs_a_person). notation_variant_only, a pure notation
    difference with no rename, is not covered by this item and is
    unchanged: it still takes the AUTO-MERGE-gate-checked path.

14. LINK AS RELATED か REJECT MATCH か(記録の書き込みが提案されている場合)。
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

15. TM系(有効日の先後)における R1 か R2 か NO-DATA か。この判定は境界規則1
    から14のいずれにも属さず、TM系のみが用いる語彙(R1、R2、NO-DATA)に答える
    三つの規則で行う。判定は入力の as_of(設問が問う時点)と
    candidate_effective_date(後継レコードが効力を持つ日)の先後関係のみで
    決まる。allowed_answers に "R1" が含まれる場合に限り作動し、track の値
    では作動しない: U1からU4のTM風の設問は決定語彙(KEEP SEPARATE など)で
    答えるため、この三規則の対象外のまま既存のはしごに届く。
    ・temporal_at_or_after_successor_is_r2: as_of が candidate_effective_date
      以後であれば R2(後継レコードが有効)。
    ・temporal_before_successor_is_r1: as_of が candidate_effective_date より
      前で、かつ lifecycle が closed でなければ R1(先行レコードがなお有効)。
    ・temporal_gap_is_nodata: as_of が candidate_effective_date より前で、
      lifecycle が closed であり、かつ入力に prior_valid_to(先行レコード
      自身の有効終了日)が無い場合は NO-DATA。閉鎖から後継開始までの休止
      期間(空白)が無いとは言い切れないため、推測せず正直に NO-DATA と
      答える。
    月精度の日付が同一年月内で衝突する場合(いずれかの側に日が無く、年月
    のみ一致する場合)、先後が確定できないためこの三規則のいずれも作動し
    ない。
    EN: TM-track (which record is in force as of a stated date): R1 versus
    R2 versus NO-DATA. This decision belongs to none of boundary rules 1
    to 14; it is made by three rules that answer only the vocabulary the
    TM track itself uses (R1, R2, NO-DATA), based solely on the order
    between the input's as_of (the date the question asks about) and
    candidate_effective_date (the date the later, successor record takes
    effect). The three rules fire only when "R1" is a member of the
    case's own allowed_answers, never on the track value: U1 to U4's
    TM-flavored cases answer from the decision vocabulary (KEEP SEPARATE
    and the like) instead, and reach the existing ladder untouched.
    - temporal_at_or_after_successor_is_r2: as_of is at or after
      candidate_effective_date: R2 (the successor record governs).
    - temporal_before_successor_is_r1: as_of is before
      candidate_effective_date and lifecycle is not closed: R1 (the
      earlier record is still in force).
    - temporal_gap_is_nodata: as_of is before candidate_effective_date,
      lifecycle is closed, and the input carries no prior_valid_to (the
      earlier record's own valid-to date): NO-DATA. A dormancy gap
      between the closure and the successor's start cannot be ruled out,
      so the honest answer is NO-DATA rather than a guess.
    A pair of dates that collide at month precision (either side missing
    a day, both falling in the same year and month) has an unknowable
    order, so none of the three rules fires. (review-temporal-track-
    2026-09-06.md; engine mutation ids
    temporal_at_or_after_successor_is_r2, temporal_before_successor_is_r1
    and temporal_gap_is_nodata.)
## Rule id map

`scripts/jbeq_decide.py` has grown well past the eleven boundary rules named
above: rounds 5 through 9 added gates, guards and top-level rules with their
own ids, and this addendum was never updated to say which prose paragraph,
if any, each one implements. A person reading a `decisions.jsonl` row's
`rule_fired` value has had no way to find the rule text behind it.

This section closes that gap with one entry per id `decide()` can write to
`rule_fired`, plus every id `JBEQ_DECIDE_DISABLE_RULES` accepts to disable
one rule or gate on its own (the module docstring's own list, near
`scripts/jbeq_decide.py` lines 280 to 299). `scripts/test_jbeq_addendum_map.py`
parses both lists straight out of the source with Python's `ast` module (it
never retypes them by hand) and asserts every id below is a member of the
same set the code can actually produce, so this table cannot drift from the
engine silently again. Some ids exist only to gate or guard another rule and
are never themselves written to `rule_fired`; those are marked as such.

- `1`, addendum heading: none. Implements the base vocabulary's boundary
  rule 1 (`scripts/jbeq_mdm.py`'s `VOCABULARY`, quoted verbatim in
  `benchmarks/jbeq/README.md`), not a rule added by this addendum. Reads
  `stated_relation`; answers LINK AS RELATED when it is not `"none"`, else
  KEEP SEPARATE. The terminal default the whole precedence order falls
  through to when nothing earlier fired. Mutation id: none, rule 1 is
  always active, because the bare value `"1"` in `JBEQ_DECIDE_DISABLE_RULES`
  disables every other rule instead of this one specifically, and rule 1
  cannot be named individually without colliding with that sentinel. Code:
  `scripts/jbeq_decide.py:1411-1419`.
- `2`, addendum heading: none. Implements the base vocabulary's boundary
  rule 2 (a stated fact refutes the proposed identity), narrowed to the
  `stated_difference == "different_legal_entity"` case specifically. Reads
  `stated_difference`, then (through `gated_reject`) `requested_action` and
  `requested_relation_type`. Answers REJECT MATCH when a proposal is on the
  table (`_has_proposal`), else KEEP SEPARATE (round 8's REJECT-versus-KEEP
  boundary fix, `proposal_gate`). Mutation id: `2`. Code:
  `scripts/jbeq_decide.py:1071-1096`.
- `5`, addendum heading: Rule 5 (site-only, one-to-many object). Mutation
  id: `5`. Code: `scripts/jbeq_decide.py:1151-1157`.
- `7`, addendum heading: Rule 7 (group company-code records). Mutation id:
  `7`. Code: `scripts/jbeq_decide.py:1159-1164`.
- `A`, addendum heading: Rule 8 (ESCALATE versus NO-DATA on a blank,
  corroborated field), also implementing the base vocabulary's boundary
  rule 3. Reads `blank_fields`, `corroborating_facts`, `identifiers`,
  `contradicted_attributes`, `effective_dates`, `hierarchy_parents`.
  Mutation id: `A`. Code: `scripts/jbeq_decide.py:1366-1409`.
- `B`, addendum heading: Rule 9 (the merge ladder, evidence strength
  before confirmation reason), also implementing the base vocabulary's
  boundary rule 4. Reads `evidence_strength`, `evidence_reasons`,
  `contradicted_attributes`, `irreversible`, `history_exists`. Its
  strong-evidence, no-reason branch calls the AUTO-MERGE safety gate
  (`_auto_merge_blocked`, the five `gate-*` ids below) before answering
  AUTO-MERGE. Mutation id: `B`. Code: `scripts/jbeq_decide.py:1305-1364`.
- `C`, addendum heading: Rule 10 (identifier-domain mismatch or a stated
  tenant boundary). Reads `tenant_boundary`, `stated_difference`. Mutation
  id: `C`. Code: `scripts/jbeq_decide.py:1007-1016`.
- `D`, addendum heading: Rule 11 (independent hierarchy types). Reads
  `hierarchy_parents`. Mutation id: `D`. Code:
  `scripts/jbeq_decide.py:995-1005`.
- `L`, addendum heading: rule 6, for the `same_area_different_lot` value
  only (added round 12, 2026-09-07; the rest of `L` was added round 5 to
  7, after this file's own numbered rules were written, and never given a
  matching heading here). Reads `location_comparison`. Answers REJECT
  MATCH (gated) for `different_administrative_area`, ESCALATE for
  `internally_inconsistent`, AUTO-MERGE (through the same safety gate as
  rule B) for `notation_variant_only` and `same_area_renamed`, SUGGEST
  MERGE for `same_chiban_different_notation`, KEEP SEPARATE for
  `same_area_different_lot` (own id
  `same_area_different_lot_keeps_separate`, below); `different_unit_in_building`
  is read separately, by `rule_r` below. Mutation id: `L`. Code:
  `scripts/jbeq_decide.py:1098-1149`.
- `link_vs_reject`, addendum heading: none. Reads `requested_relation_type`,
  `hierarchy_parents`, `requested_parent`, `stated_difference`,
  `stated_relation`. Runs ahead of rule 2 in the precedence order so a
  proposal the input's own hierarchy facts refute is never confused with a
  case that also states a relation: answers REJECT MATCH when a requested
  hierarchy write conflicts with the stated type (HI-05) or direction
  (HI-02, `requested_parent`), or LINK AS RELATED when
  `stated_difference == "different_legal_entity"` but an identity-bearing or
  relationship-only `stated_relation` is also present (closing EO-02,
  EO-09). Mutation id: `link_vs_reject`. Code:
  `scripts/jbeq_decide.py:1020-1096`.
- `rule_r`, addendum heading: none. Fires only when `stated_relation`
  is `"none"`. Reads `authoritative_identifier`, `identifiers`,
  `object_type_a`, `object_type_b`, `blank_fields`, `evidence_reasons`,
  `contradicted_attributes` (the stated-fact guard), `lifecycle`,
  `location_comparison`. Answers REJECT MATCH (gated) or KEEP SEPARATE
  across several distinct refuting facts: a conflicting authoritative
  identifier, two identifiers that share a value across domains or carry
  different `legal_person_type`, a mismatched `object_type`, a closed,
  relocated or reused-identifier lifecycle, or an address matching only
  down to the wrong building unit. Added round 5 to restore the
  REJECT-versus-KEEP cases boundary rule 2's generic form does not reach
  on its own. Mutation id: `rule_r`. Code:
  `scripts/jbeq_decide.py:1192-1303`.
- `site_store`, addendum heading: none. Reads
  `distinct_operational_attributes`, `evidence_strength`. Answers LINK AS
  RELATED when each side carries its own delivery, booking or pricing
  target, unless evidence is weak (see `site_store_weak_guard` below), in
  which case the case falls through to rule B's weak-evidence branch
  instead. Mutation id: `site_store`. Code:
  `scripts/jbeq_decide.py:1166-1190`.
- `track-unsupported`, addendum heading: none. Reads `track`. Answers
  NO-DATA naming the track when it is `"survivorship"` or `"requirements"`
  (`UNSUPPORTED_TRACKS`): no rule handed to this module defines either
  track's own answer vocabulary. Mutation id: none, `UNSUPPORTED_TRACKS`
  is a fixed set, not a rule `JBEQ_DECIDE_DISABLE_RULES` can turn off. Code:
  `scripts/jbeq_decide.py:988-993`.
- `safety-net`, addendum heading: none. Reached only from inside `finish()`
  when a rule's chosen answer is outside the case's own `allowed_answers`
  and `_remap_to_allowed` (see `allowed_answers_remap` below) also finds
  nothing reachable there. Answers NO-DATA naming the mismatch. Mutation id:
  none directly, though disabling `allowed_answers_remap` sends every
  mismatch straight here instead of trying the remap first. Code:
  `scripts/jbeq_decide.py:925-955`.
- `validation`, addendum heading: none. Checks the fact sheet's own keys
  against `REQUIRED_FIELDS`. Answers NO-DATA naming the missing field(s)
  when any required key is absent, or when the sheet itself is not a JSON
  object (`cmd_decide`'s own inline case for a malformed entry). Mutation
  id: none. Code: `scripts/jbeq_decide.py:903-909` and
  `scripts/jbeq_decide.py:1554-1556`.
- `unrecognized-value`, addendum heading: none. Checks every field in
  `_ENUM_FIELDS`, plus `evidence_reasons` and `identifiers[].legal_person_type`,
  against the enums `_load_enums()` reads from
  `benchmarks/jbeq/mdm/fact-sheet-schema.json`'s `_enums` key. Answers
  NO-DATA naming the field and its off-enum value, unless `allow_unrecognized`
  is set (the historical re-decision path for sheets extracted before this
  check existed). Mutation id: none, this is gated by the separate
  `allow_unrecognized` flag, not by `JBEQ_DECIDE_DISABLE_RULES`. Code:
  `scripts/jbeq_decide.py:911-920`.
- `gate-object-type`, addendum heading: none. One item of the AUTO-MERGE
  safety gate (`_auto_merge_blocked`), reached only from inside rule B's
  strong-evidence branch or rule L's notation-only AUTO-MERGE path. Reads
  `object_type_a`, `object_type_b`. Answers LINK AS RELATED when the two
  sides are different kinds of record, blocking the merge. Mutation id:
  `object_type`. Code: `scripts/jbeq_decide.py:764-773`.
- `gate-lifecycle`, addendum heading: none. Same gate as above. Reads
  `lifecycle`. Answers KEEP SEPARATE for `closed` or `identifier_reused`
  (and for `relocated` too, if `relocated_not_a_bar` is itself disabled), or
  SUGGEST MERGE for `relocated` on its own (a compatible lifecycle of the
  same object, so it bars AUTO-MERGE only, not every merge answer).
  Mutation id: `lifecycle` (the `relocated` split carries its own id,
  `relocated_not_a_bar`, below). Code: `scripts/jbeq_decide.py:775-800`.
- `gate-authoritative-identifier`, addendum heading: none. Same gate.
  Reads `authoritative_identifier`. Answers SUGGEST MERGE when it is not
  `"aligned"`. Mutation id: `authoritative_identifier`. Code:
  `scripts/jbeq_decide.py:802-810`.
- `gate-stated-relation`, addendum heading: none. Same gate. Reads
  `stated_relation`. Answers LINK AS RELATED when it is a
  `RELATIONSHIP_ONLY_RELATIONS` value (round 6's identity-versus-relationship
  split; see `relation_partition` below). Mutation id: `stated_relation_gate`.
  Code: `scripts/jbeq_decide.py:812-833`.
- `gate-temporal`, addendum heading: none. Same gate. Reads
  `effective_dates.conflict`. Answers KEEP SEPARATE when it is true.
  Mutation id: `temporal`. Code: `scripts/jbeq_decide.py:835-843`.
- `rules-disabled-for-test`, addendum heading: none, and it never appears
  in a real run: returned unconditionally, before any field is even read,
  only when `JBEQ_DECIDE_DISABLE_RULES` carries the bare value `"1"` (the
  sentinel meaning "disable every rule"). Answers NO-DATA. Mutation id:
  none directly, it is the effect of the `"1"` sentinel itself, not a
  rule that sentinel disables. Code: `scripts/jbeq_decide.py:895-901`.

Twelve further ids exist only to gate or guard one of the rules above; none
of them is ever itself written to `rule_fired`.

- `object_type`, disable id for `gate-object-type` (above). Disabling it
  removes the object-type check from the AUTO-MERGE safety gate, so a merge
  across two different `object_type` values is no longer blocked there.
  Code: `scripts/jbeq_decide.py:764`.
- `lifecycle`, disable id for `gate-lifecycle` (above). Disabling it
  removes the lifecycle check from the AUTO-MERGE safety gate, so a closed,
  reused-identifier or relocated record is no longer blocked from
  AUTO-MERGE there. Code: `scripts/jbeq_decide.py:775`.
- `authoritative_identifier`, disable id for `gate-authoritative-identifier`
  (above). Disabling it removes the identifier-alignment check from the
  AUTO-MERGE safety gate. Code: `scripts/jbeq_decide.py:802`.
- `stated_relation_gate`, disable id for `gate-stated-relation` (above).
  Disabling it removes the stated-relation check from the AUTO-MERGE safety
  gate entirely, regardless of which `stated_relation` value is present
  (see `relation_partition` just below, which narrows what the gate fires
  on rather than removing it outright). Code: `scripts/jbeq_decide.py:812`.
- `temporal`, disable id for `gate-temporal` (above). Disabling it removes
  the `effective_dates` conflict check from the AUTO-MERGE safety gate.
  Code: `scripts/jbeq_decide.py:835`.
- `relation_partition`, guard shared by `gate-stated-relation` and
  `link_vs_reject`'s `different_legal_entity` branch (round 6 repair).
  Reads `stated_relation` against `IDENTITY_BEARING_RELATIONS` and
  `RELATIONSHIP_ONLY_RELATIONS`. Disabling it reverts both call sites to
  firing or confirming on any non-`"none"` `stated_relation`, including
  `corporate_number_shared` and `same_site_only`, which is the pre-round-6
  defect (EO-03, MM-04, MM-07, AD-10). Code: `scripts/jbeq_decide.py:824`
  and `scripts/jbeq_decide.py:1080`.
- `allowed_answers_remap`, guard inside `finish()`. When a rule's chosen
  answer is outside the case's own `allowed_answers`, this id controls
  whether `_remap_to_allowed` walks `jbeq_mdm.CAUTION_RANK` to the nearest
  allowed answer first; disabling it reverts straight to the pre-round-7
  `safety-net` NO-DATA answer on every mismatch. Code:
  `scripts/jbeq_decide.py:937`.
- `proposal_gate`, guard inside `gated_reject()`, used by rules `2`, `L`
  and `rule_r`. Reads `requested_action` and `requested_relation_type`
  (through `_has_proposal`). Disabling it restores REJECT MATCH
  unconditionally at every `gated_reject` call site (the pre-round-8
  behaviour), removing the KEEP SEPARATE answer for a refuted fact with no
  proposal ever on the table. Code: `scripts/jbeq_decide.py:977`.
- `relocated_not_a_bar`, guard splitting the `relocated` lifecycle value
  out of `gate-lifecycle`'s and `rule_r`'s "bars everything" clauses (round
  9 repair). Disabling it reverts `relocated` to being treated exactly like
  `closed` and `identifier_reused` (barring AUTO-MERGE and reaching the
  merge ladder at all) in both places. Code: `scripts/jbeq_decide.py:786`
  and `scripts/jbeq_decide.py:1269`.
- `rule_r_blank_fields_guard`, guard inside `rule_r`'s `object_type` and
  `lifecycle` clauses. Reads `blank_fields`, `evidence_reasons`,
  `contradicted_attributes`. When any of those is non-empty, an inferred
  `object_type` or lifecycle difference is not allowed to outrank a stated
  absence or conflict (AD-09, U-38); disabling it lets both clauses fire
  unconditionally again. Code: `scripts/jbeq_decide.py:1249`.
- `rule_r_lifecycle_keep`, guard on `rule_r`'s lifecycle-clause answer.
  When active (the default), a closed, relocated or reused-identifier fact
  reached through `rule_r` answers KEEP SEPARATE, matching
  `gate-lifecycle`'s answer for the identical fact (round 7 repair, U-36);
  disabling it reverts `rule_r`'s own answer to REJECT MATCH (gated), which
  used to contradict `gate-lifecycle` on the same fact. Code:
  `scripts/jbeq_decide.py:1282`.
- `site_store_weak_guard`, guard on the `site_store` rule (above). Reads
  `evidence_strength`. When evidence is `"weak"`, this guard blocks
  `site_store` from firing, letting the case fall through to rule B's
  weak-evidence branch instead of LINK AS RELATED (EO-07); disabling it
  lets `site_store` fire regardless of evidence strength again. Code:
  `scripts/jbeq_decide.py:1180`.

Addendum rule 6, above, has no engine id at all: no field in `decide()`'s
`REQUIRED_FIELDS`, and no key in `benchmarks/jbeq/mdm/fact-sheet-schema.json`,
carries a "the reading matches but the kanji spelling differs" or
"notational closeness" signal in the first place, so no code path in
`scripts/jbeq_decide.py` could apply rule 6's guidance even if it tried to.
`link_vs_reject`, the rule closest to it in subject (weighing a stated
refuting fact against a stated relation), reads `stated_difference` and
`stated_relation` only and never reads anything about a reading match or a
notation's closeness. Rule 6 is therefore **not implemented by any rule** in
this engine; it is realized, if at all, only upstream of `decide()`, in
whatever extracts the fact sheet, by that extractor choosing not to write a
refuting `stated_difference` or `tenant_boundary` value off mere reading
similarity in the first place.

**Amended 2026-09-07** (review-rule6-2026-09-07.md ranking item 1, quoted
verbatim): "Rule 6 is not vacuous. No enum field carries a reading or
notation signal, but `contradicted_attributes` is free text and does
carry it (name_notation, company_name_kanji, address_banchi,
item_code_notation, lot_number, at least 11 occurrences across the frozen
70 and U1 to U4). Every reader of that field answers KEEP SEPARATE,
ESCALATE, or blocks a refusal, so no path reaches REJECT MATCH from it.
The one measured rule 6 violation, U3 W-03, refuses through
`location_comparison=different_administrative_area` at
`scripts/jbeq_decide.py:1292`." That line number is the review's own, read
against the tree as it stood that morning, before this round's edits
shifted it. The violation is closed the same round this map is amended:
rule L gained `same_area_different_lot` (own id
`same_area_different_lot_keeps_separate`, KEEP SEPARATE), so rule 6 now
has one engine id after all, narrower than the paragraph above claims.
- `rule_c_hierarchy_dimension`, addendum heading: Rule 10, the hierarchy dimension value (2026-09-06). Gates rule C on stated_difference different_hierarchy_dimension: two nodes in different hierarchy dimensions are never the same node whatever their names. Answers REJECT MATCH through rule C. Mutation id: `rule_c_hierarchy_dimension`. Code: `scripts/jbeq_decide.py:1086`.
- `medium_needs_confirmation`, addendum heading: Rule 9, the medium branch (2026-09-06, U3 fix F1). Rule B: medium evidence with no confirmation reason stated apart from the evidence itself (irreversible false and history_exists false) answers ESCALATE; an unvalidated crosswalk is never counted twice. Mutation id: `medium_needs_confirmation`. Code: `scripts/jbeq_decide.py:1511`.
- `relocated_address_not_a_refutation`, addendum heading: Rule 12, a relocation never refutes identity (2026-09-06, U3 fix F3). When lifecycle is relocated, rule L does not read different_administrative_area as a refutation. Mutation id: `relocated_address_not_a_refutation`. Code: `scripts/jbeq_decide.py:1221`.
- `site_store_write_refutes`, addendum heading: Item 14, a refuting fact beats a stated relation when the proposal is a write (2026-09-06, U4 fix F1b). Inside site_store, a record_write proposal takes gated_reject instead of LINK AS RELATED. Mutation id: `site_store_write_refutes`. Code: `scripts/jbeq_decide.py:1340`.
- `link_vs_reject_write_refutes`, addendum heading: Item 14, a refuting fact beats a stated relation when the proposal is a write (2026-09-06, U4 fix F1a). In link_vs_reject, confirms_relation does not apply to a record_write proposal, so the stated refutation wins. Mutation id: `link_vs_reject_write_refutes`. Code: `scripts/jbeq_decide.py:1175`.
- `same_site_only_never_a_relation`, addendum heading: Item 14, a shared address is never the relation (2026-09-06, U4 fix F2). The rule_r guard and boundary rule 1 read stated_relation same_site_only as none. Mutation id: `same_site_only_never_a_relation`. Code: `scripts/jbeq_decide.py:952`.
- `renamed_area_needs_a_person`, addendum heading: Item 13, a renamed area (founder ruling 2026-09-06). Rule L answers SUGGEST MERGE on location_comparison same_area_renamed, never AUTO-MERGE; disabled, the old notation-variant path runs. Mutation id: `renamed_area_needs_a_person`. Code: `scripts/jbeq_decide.py:1271`.
- `same_area_different_lot_keeps_separate`, addendum heading: Rule 6, same area differing only by lot number (2026-09-07, review-rule6-2026-09-07.md ranking item 2, closes U3 W-03). Rule L answers KEEP SEPARATE on location_comparison same_area_different_lot, never REJECT MATCH and never a merge; disabled, the case falls through to whichever rule would apply next, exactly as if location_comparison carried no signal at all. Mutation id: `same_area_different_lot_keeps_separate`. Code: `scripts/jbeq_decide.py:1330`.
- `rule5_hoist`, addendum heading: Rule 5, its position before the location ladder (2026-09-06, U3 fix F2; QA finding M1). The hoisted rule 5 block answers KEEP SEPARATE for a one-to-many object on a shared address before the notation-variant branch's AUTO-MERGE gates; disabling this id alone re-lowers the block while rule 5's body keeps its own id 5. Mutation id: `rule5_hoist`. Code: `scripts/jbeq_decide.py:1408`.
- `temporal_at_or_after_successor_is_r2`, addendum heading: Temporal track, the record in force at the asked date (2026-09-06 review, landed 2026-09-07). Gated on R1 being in the case's allowed answers: as_of at or after candidate_effective_date answers R2. Mutation id: `temporal_at_or_after_successor_is_r2`. Code: `scripts/jbeq_decide.py:1121`.
- `temporal_before_successor_is_r1`, addendum heading: Temporal track, the record in force at the asked date. Gated on R1 in allowed: as_of before candidate_effective_date while the earlier record is not closed answers R1. Mutation id: `temporal_before_successor_is_r1`. Code: `scripts/jbeq_decide.py:1131`.
- `temporal_gap_is_nodata`, addendum heading: Temporal track, a stated closure gap. Gated on R1 in allowed: as_of before candidate_effective_date with lifecycle closed and no prior_valid_to answers NO-DATA, because a dormancy gap cannot be ruled out. Mutation id: `temporal_gap_is_nodata`. Code: `scripts/jbeq_decide.py:1140`.

# Where the policies come from

The brand "Masr Connect" is fictional, so the demo never pretends to be a real company. Its rules follow the real public terms of Egyptian operators and the regulator, checked on 8 Oct 2026 and rewritten in our own words. Each policy section ends with a "Based on" line linking to its sources. In the database, the `policy_sections.source_ids` column holds the source keys and `policy_sources` holds the links.

## Taken from real public terms

| Topic | The real rule we use | Source |
|---|---|---|
| Recharge tax | Since Dec 2024 a 10 EGP card sells for 13 EGP and gives 9.10 credit, and a 20 EGP card sells for 26 EGP and gives 18.20. | [Al-Dostor, Dec 2024](https://www.dostor.org/4886029) |
| Recharge SMS | Operators must text every deduction taken from a recharge. | [NTRA](https://www.tra.gov.eg/en/the-national-telecommunication-regulatory-authority-obligates-mobile-operators-to-notify-users-of-any-deductions-from-charged-credit-per-each-re-charge) |
| Prepaid line validity | After 90 days without paid use the line can only receive calls for 5 days, is then suspended for 10 days, and is cancelled on day 105. | [WE FAQ](https://te.eg/en/about-te/faq) |
| Salefny | You can borrow when your balance is under 5 EGP. The fee tiers (1.50 on 5, 3 on 10, 6 on 20, 9 on 30, 12.50 on 50) are taken when you receive the credit, and the loan is repaid from your next recharge. | [e& Egypt](https://www.eand.com.eg/portal/pages/services/out_of_credit_en.html), [Vodafone](https://web.vodafone.com.eg/en/shokran-services) |
| Balance transfer | 1 to 50 EGP per transfer, with a 2% fee (minimum 0.20). | [Vodafone](https://web.vodafone.com.eg/en/shokran-services), [WE FAQ](https://te.eg/en/about-te/faq) |
| Bundles | Usage alerts at 50%, 80% and 100%. Internet bundles renew every 30 days and Control plans every 28. Control units carry over only if renewed in time. Your remaining MB stay when you switch bundles before renewal. When a bundle runs out you pay as you go or buy an extra GB. | [WE FAQ](https://te.eg/en/about-te/faq) |
| Consent and customer rights | Nothing is added without consent, there are free codes to check bundle details, free SMS for every deduction, at least a week's notice before a cut-off for late payment, a detailed bill with a free e-bill, and 24-hour support. | [NTRA customer rights](https://www.telecomreviewafrica.com/articles/general-news/2219-egypt-s-ntra-issues-a-list-of-rights-for-mobile-internet-customers/) |
| Lines per ID | 10 voice and 5 data lines per national ID, or 5 and 5 per passport. A foreigner's line stops a month after their residency expires. | [NTRA SIM rules](https://www.tra.gov.eg/en/mobile-sim-cards-sales-regulations-for-individuals/) |
| SIM swap | In person only, with the original ID, which must match the line's records, plus a signed form. The fee is 5 EGP. | [NTRA SIM rules](https://www.tra.gov.eg/en/mobile-sim-cards-sales-regulations-for-individuals/), [WE FAQ](https://te.eg/en/about-te/faq) |
| New line and ownership transfer | A new line costs 81.23 EGP. Transferring ownership costs 25 EGP and needs both IDs in a store. | [WE FAQ](https://te.eg/en/about-te/faq) |
| eSIM | Existing customers switch in the app with a photo of their ID. New customers get one in a store. | [Orange Egypt eSIM](https://meatechwatch.com/2024/12/06/orange-egypt-launches-esim-service-for-enhanced-mobile-connectivity/) |
| Moving your number | Free, done within 24 hours. You must have owned the line for 4 months and paid the latest bill. The operator can't refuse. | [NTRA](https://www.tra.gov.eg/en/ntra-imposes-a-fine-of-20650-000-pounds-on-mobile-operators-for-violating-mobile-number-portability-service-regulations/) |
| Moving home internet | Free, within 72 hours: the old provider cancels within 24 hours and the new one activates within 48. You pay the last bill and any router installments first. | [NTRA](https://www.tra.gov.eg/en/new-rules-to-facilitate-fixed-internet-portability-service-to-take-place-within-72-hours/) |
| Roaming | Prepaid and Control lines can turn it on free at any time. Postpaid lines need 4 paid bills or a 750 EGP deposit, and new postpaid lines are capped at 250 EGP. | [Vodafone roaming](https://web.vodafone.com.eg/en/roamingmanagement) |
| Home internet renewal | Renewing early starts a new 30-day cycle, and speed drops sharply once the quota runs out. | [WE early renewal](https://mobilemasr.com/en/blogs/renewing-your-we-home-internet-package-before-the-due-date) |
| Complaints | You can escalate to NTRA on 155, the My NTRA app, its website, e-mail or WhatsApp. NTRA orders refunds after it validates a complaint. | [NTRA 2024 report](https://www.tra.gov.eg/en/?p=10448), [WE FAQ](https://te.eg/en/about-te/faq) |

## Masr Connect's own choices (no operator publishes these)

These are internal business rules a real company sets privately, so they're invented:
- the agent's refund limit (200 EGP) and supervisor limit for bill adjustments (500 EGP)
- the 24-hour / under-5% bundle refund rule
- the 25 EGP late fee, 15-day due date and 30/90-day suspension steps
- complaint response times (3, 5 and 7 days)
- outage compensation
- home internet installation, fault visit fee and relocation fee
- early-termination and installment terms
- the identity check (line number plus the last 4 digits of the national ID)

Two figures are our own extensions of a real rule. Digital recharges keep the same 91% that cards give, and the 30/50 EGP Salefny tiers are picked from e&'s published list. Plan names and bundle prices are invented.

## Not used

Operator pages that couldn't be reached or gave no numbers (several Orange and e& help pages) were skipped. A 2016 tax article was ignored because December 2024 card prices replaced it.

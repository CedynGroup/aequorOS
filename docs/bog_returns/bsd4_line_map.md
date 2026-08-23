# BSD4 — Sectoral Analysis of Overdrafts, Loans and Other Advances: line / cell map

**Official workbook:** `FORM BSD4 REVISED.xls` · **Frequency:** monthly · **Time limit:** 14 days · **Basis:** solo · **Unit:** ¢'Million (amounts; "Enter amounts to nearest million, omitting 000,000") — `No. of Cust.` columns are counts and Annex column D is a percentage (both `unscaled`)

**Sheets (3, official order):** `BSD4`, `4a Annexure`, `4b Annexure`

Generated from `bog_forms/linemaps/bsd4.py` + `sources_ext/bsd4.py` + `layouts/BSD4.json` (script over the line map and layout — do not hand-edit; regenerate).

## Guide rules applied (BSD4 notes + General Notes)

- Month-end, close of business; 14 calendar days; Section 53(1) of the Banking Act 2004.
- **"The customers to be included in Form BSD4 relate to domestic accounts only"** — loans to counterparties whose `resident` flag is **False** are excluded from the main sheet (they appear on Annex 4a *Non-residents* / Annex 4b regions). NULL residency = resident.
- **Acceptance credits and consortium loans are included** and classified under the appropriate sector — the map takes every `LOAN` position of the bank's own current-generation book (syndication participants' shares are not the bank's assets and are not ingested as such).
- **Grand total ties to BSD2 item 8 sub-total**: the borrower-class rules below are BSD2 §8's own conventions (`SOVEREIGN`, `GOVERNMENT_ENTITY` + `borrower_class`, cocoa syndication, `CORPORATE`/`SME`, `RETAIL_INDIVIDUAL`), so a loan placed on BSD2 §8 lands in exactly one BSD4 column group and vice-versa (see *Residual* for what neither form can place).
- **Sector = the customer's industry** (Guide: "the type of industry in which a customer is involved"): the documented `sector` attribute is read from the counterparty's `attributes` and may be overridden per facility on the position snapshot's `attributes`.
- **Performing / non-performing** = the platform's loan classification rule (`fact_derivation._classify_loans`): IFRS 9 **stage 3 = non-performing**, otherwise performing.
- **No. of Cust.** = COUNT DISTINCT `counterparty_id` in the cell — never a sum of loans.
- **Amounts** are cedi equivalents: `balance_ghs` snapshot attribute when supplied, raw balance for base-currency loans, otherwise the platform's preferred FX spot at period end (`market_data_sources.preferred_fx_spot`; raw balance if no spot exists).

## Sector taxonomy — the documented `sector` attribute

The value is the **key** below (case-insensitive) or, where the official leaf label is unique within the form, that label (e.g. `Cocoa Production`, `Salary Credit`; the manufacturing sub-lines repeat between *FOR EXPORT* and *FOR HOME MARKET* and therefore need the key). A book with **no** `sector` attribute on any LOAN yields every sector cell **input_required** (note: *sector classification attribute required*) — nothing is inferred from product codes or names. Once the book is classified, loans whose `sector` is absent or unrecognised fall to **9. MISCELLANEOUS**, which the Guide defines as "activities of bank customers not elsewhere specified or bank customers whose activities are not adequately described".

| Row | Official line (section › leaf) | `sector` key |
|---|---|---|
| 10 | 1. AGRICULTURE FORESTRY & FISHING › (i)  Cocoa Production | `agriculture.cocoa_production` |
| 11 | 1. AGRICULTURE FORESTRY & FISHING › (ii)  Livestock Breeding | `agriculture.livestock_breeding` |
| 12 | 1. AGRICULTURE FORESTRY & FISHING › (iii) Poultry Farming | `agriculture.poultry_farming` |
| 13 | 1. AGRICULTURE FORESTRY & FISHING › (iv) Other Agriculture | `agriculture.other` |
| 14 | 1. AGRICULTURE FORESTRY & FISHING › (v)  Forestry | `agriculture.forestry` |
| 15 | 1. AGRICULTURE FORESTRY & FISHING › (vi) Logging | `agriculture.logging` |
| 16 | 1. AGRICULTURE FORESTRY & FISHING › (vii) Fishing | `agriculture.fishing` |
| 19 | 2.  MINING & QUARRYING › (I)  Bauxite | `mining.bauxite` |
| 20 | 2.  MINING & QUARRYING › (ii) Diamonds | `mining.diamonds` |
| 21 | 2.  MINING & QUARRYING › (iii) Gold | `mining.gold` |
| 22 | 2.  MINING & QUARRYING › (iv) Manganese | `mining.manganese` |
| 23 | 2.  MINING & QUARRYING › (v)  Quarrying | `mining.quarrying` |
| 24 | 2.  MINING & QUARRYING › (vi) Other Mining Activity | `mining.other` |
| 28 | 3.  MANUFACTURING › A.  FOR EXPORT › (i)  Food, Drink & Tobacco | `manufacturing.export.food_drink_tobacco` |
| 29 | 3.  MANUFACTURING › A.  FOR EXPORT › (ii) Textiles, Clothing & Footwear | `manufacturing.export.textiles_clothing_footwear` |
| 30 | 3.  MANUFACTURING › A.  FOR EXPORT › (iii) Sawmilling & Wood Processing | `manufacturing.export.sawmilling_wood_processing` |
| 31 | 3.  MANUFACTURING › A.  FOR EXPORT › (iv)  Paper pulp & Paper products | `manufacturing.export.paper_pulp_products` |
| 32 | 3.  MANUFACTURING › A.  FOR EXPORT › (v)  Chemicals and Fertilizers | `manufacturing.export.chemicals_fertilizers` |
| 33 | 3.  MANUFACTURING › A.  FOR EXPORT › (vi) Iron and Steel | `manufacturing.export.iron_steel` |
| 34 | 3.  MANUFACTURING › A.  FOR EXPORT › (vii) Boat/Ship Building and repairs | `manufacturing.export.boat_ship_building` |
| 35 | 3.  MANUFACTURING › A.  FOR EXPORT › (viii) Manufacturing of Motor Vehicles | `manufacturing.export.motor_vehicles` |
| 36 | 3.  MANUFACTURING › A.  FOR EXPORT › (ix) Other Unclassified | `manufacturing.export.other` |
| 38 | 3.  MANUFACTURING › B.  FOR HOME MARKET › (i)  Food, Drink & Tobacco | `manufacturing.home.food_drink_tobacco` |
| 39 | 3.  MANUFACTURING › B.  FOR HOME MARKET › (ii) Textiles, Clothing & Footwear | `manufacturing.home.textiles_clothing_footwear` |
| 40 | 3.  MANUFACTURING › B.  FOR HOME MARKET › (iii) Sawmilling and Wood Processing | `manufacturing.home.sawmilling_wood_processing` |
| 41 | 3.  MANUFACTURING › B.  FOR HOME MARKET › (iv) Paper,Pulp & Paper Products | `manufacturing.home.paper_pulp_products` |
| 42 | 3.  MANUFACTURING › B.  FOR HOME MARKET › (v) Chemicals and Fertilizer | `manufacturing.home.chemicals_fertilizers` |
| 43 | 3.  MANUFACTURING › B.  FOR HOME MARKET › (vi) Iron and Steel | `manufacturing.home.iron_steel` |
| 44 | 3.  MANUFACTURING › B.  FOR HOME MARKET › (vii) Boat/Ship building and repairs | `manufacturing.home.boat_ship_building` |
| 45 | 3.  MANUFACTURING › B.  FOR HOME MARKET › (viii) Manufacturing of Motor Vehicles | `manufacturing.home.motor_vehicles` |
| 46 | 3.  MANUFACTURING › B.  FOR HOME MARKET › (ix)  Other Unclassified | `manufacturing.home.other` |
| 49 | 4.  CONSTRUCTION › (i)  Construction & Works | `construction.construction_works` |
| 50 | 4.  CONSTRUCTION › (ii)  Building Construction | `construction.building_construction` |
| 53 | 5.  ELECTRICITY, GAS & WATER › (i) Electric light & Power | `utilities.electricity` |
| 54 | 5.  ELECTRICITY, GAS & WATER › (ii) Gas Manufacture & Distribution | `utilities.gas` |
| 55 | 5.  ELECTRICITY, GAS & WATER › (iii) Water Supply | `utilities.water` |
| 59 | 6.  COMMERCE & FINANCE › (i)  Import Trade › (a) Motor Vehicle Import & Declaration | `commerce.import.motor_vehicles` |
| 60 | 6.  COMMERCE & FINANCE › (i)  Import Trade › (b) Machinery & Heavy equipment | `commerce.import.machinery_heavy_equipment` |
| 61 | 6.  COMMERCE & FINANCE › (i)  Import Trade › (c) Other Import Items | `commerce.import.other` |
| 64 | 6.  COMMERCE & FINANCE › (ii) Export Trade › (a) Cocoa Exports | `commerce.export.cocoa` |
| 65 | 6.  COMMERCE & FINANCE › (ii) Export Trade › (b) Timber Export | `commerce.export.timber` |
| 66 | 6.  COMMERCE & FINANCE › (ii) Export Trade › (c) Other Export Items | `commerce.export.other` |
| 67 | 6.  COMMERCE & FINANCE › (iii) Cocoa Marketing | `commerce.cocoa_marketing` |
| 68 | 6.  COMMERCE & FINANCE › (iv) Timber Marketing | `commerce.timber_marketing` |
| 69 | 6.  COMMERCE & FINANCE › (v)  Diamond Marketing | `commerce.diamond_marketing` |
| 70 | 6.  COMMERCE & FINANCE › (vi) Mortgage Financing | `commerce.mortgage_financing` |
| 72 | 6.  COMMERCE & FINANCE › (vii) Other Financial Institutions › (a) Hire Purchase Companies | `commerce.ofi.hire_purchase` |
| 73 | 6.  COMMERCE & FINANCE › (vii) Other Financial Institutions › (b) Insurance Companies | `commerce.ofi.insurance` |
| 74 | 6.  COMMERCE & FINANCE › (vii) Other Financial Institutions › (c) Building bodies and Corporations | `commerce.ofi.building_bodies` |
| 75 | 6.  COMMERCE & FINANCE › (viii) Other Unclassified | `commerce.other` |
| 78 | 7.  TRANSPORT,STORAGE AND COMMUNICATION › (i)  Railway transport | `transport.railway` |
| 79 | 7.  TRANSPORT,STORAGE AND COMMUNICATION › (ii) Road transport | `transport.road` |
| 80 | 7.  TRANSPORT,STORAGE AND COMMUNICATION › (iii)Ocean and Other Water transport | `transport.water` |
| 81 | 7.  TRANSPORT,STORAGE AND COMMUNICATION › (iv) Air transport | `transport.air` |
| 82 | 7.  TRANSPORT,STORAGE AND COMMUNICATION › (v)  Storage and warehousing | `transport.storage_warehousing` |
| 83 | 7.  TRANSPORT,STORAGE AND COMMUNICATION › (vi) Communications | `transport.communications` |
| 86 | 8. SERVICES › (I)  Printing, Publishing and Allied Products | `services.printing_publishing` |
| 87 | 8. SERVICES › (ii) Business Services | `services.business` |
| 88 | 8. SERVICES › (iii) Recreation Services | `services.recreation` |
| 89 | 8. SERVICES › (iv) Personal Services | `services.personal` |
| 90 | 8. SERVICES › (v) Salary Credit | `services.salary_credit` |
| 91 | 8. SERVICES › (vi)  Other Services including Government Services | `services.other_incl_government` |
| 93 | 9.  MISCELLANEOUS | `miscellaneous` |

## Column groups — borrower class (row 6/7 headers)

Each group has PERFORMING ¢ / NON-PERFORMING ¢ (inputs), TOTAL (template formula = SUM of the two) and No. of Cust. (input, count). Columns AP:AS are the template's grand columns (=Σ groups). An explicit `borrower_class` attribute naming a group key (or its BSD2 singular spelling) always wins over the counterparty-type rule.

| Group key | Official header | Cols (perf · NPL · total · cust.) | Counterparty rule |
|---|---|---|---|
| `central_government` | CENTRAL GOVERNMENT | B · C · D (formula) · E | counterparty_type `SOVEREIGN` |
| `public_institutions` | PUBLIC INSTITUTIONS | F · G · H (formula) · I | `GOVERNMENT_ENTITY` + `borrower_class=public_institution` (BSD2 §8(b) convention) |
| `public_enterprises` | PUBLIC ENTERPRISES | J · K · L (formula) · M | `GOVERNMENT_ENTITY` + `borrower_class=public_enterprise`, or any loan with `scheme=cocoa_syndicated` (BSD2 §8(c)) |
| `commercial_banks` | COMMERCIAL BANKS | N · O · P (formula) · Q | `BANK_OECD`, `BANK_NON_OECD` |
| `other_depository_institutions` | OTHER DEPOSITORY INSTITUTIONS | R · S · T (formula) · U | `NBFI` + `institution_class` ∈ {rural_bank, credit_union, savings_and_loans, discount_house, building_society, other_depository} (Guide ODI list) |
| `other_financial_institutions` | OTHER FINANCIAL INSTITUTIONS | V · W · X (formula) · Y | `NBFI` with any other / no `institution_class`; `MULTILATERAL_DEV_BANK` |
| `private_foreign` | PRIVATE CORPORATIONS — FOREIGN | Z · AA · AB (formula) · AC | `CORPORATE`/`SME` + `ownership=foreign` |
| `private_indigenous` | PRIVATE CORPORATIONS — INDIGENOUS | AD · AE · AF (formula) · AG | `CORPORATE`/`SME` without `ownership=foreign` (documented default: indigenous) |
| `households` | HOUSEHOLDS | AH · AI · AJ (formula) · AK | `RETAIL_INDIVIDUAL` |
| `npish` | NPISH | AL · AM · AN (formula) · AO | explicit `borrower_class=npish` on any counterparty (typically `OTHER`) |

## Sheet `BSD4` — 1,890 input cells (63 leaf rows × 10 groups × 3) · 1,498 template formulas (group TOTALs, AP:AS, section subtotals, GRAND TOTAL)

Every leaf row is bound twice by `leaf_lines`: an **amount** line (20 cells: performing + non-performing per group, ¢) and a **count** line (10 cells, `unscaled`). Both name the `bsd4.cell` resolver with the row's `sector`; the column key `<group>.<measure>` selects the cell. Status legend — **mapped**: fed from LOAN positions once the book carries `sector`; **input_required**: what every cell reports on a book without the attribute.

| Row | Official line | Status | Source | Cells bound |
|---|---|---|---|---|
| 10 | (i)  Cocoa Production | mapped (input_required until `sector` present) | `bsd4.cell` sector=agriculture.cocoa_production | 30 (20 amount ¢ + 10 count) |
| 11 | (ii)  Livestock Breeding | mapped (input_required until `sector` present) | `bsd4.cell` sector=agriculture.livestock_breeding | 30 (20 amount ¢ + 10 count) |
| 12 | (iii) Poultry Farming | mapped (input_required until `sector` present) | `bsd4.cell` sector=agriculture.poultry_farming | 30 (20 amount ¢ + 10 count) |
| 13 | (iv) Other Agriculture | mapped (input_required until `sector` present) | `bsd4.cell` sector=agriculture.other | 30 (20 amount ¢ + 10 count) |
| 14 | (v)  Forestry | mapped (input_required until `sector` present) | `bsd4.cell` sector=agriculture.forestry | 30 (20 amount ¢ + 10 count) |
| 15 | (vi) Logging | mapped (input_required until `sector` present) | `bsd4.cell` sector=agriculture.logging | 30 (20 amount ¢ + 10 count) |
| 16 | (vii) Fishing | mapped (input_required until `sector` present) | `bsd4.cell` sector=agriculture.fishing | 30 (20 amount ¢ + 10 count) |
| 19 | (I)  Bauxite | mapped (input_required until `sector` present) | `bsd4.cell` sector=mining.bauxite | 30 (20 amount ¢ + 10 count) |
| 20 | (ii) Diamonds | mapped (input_required until `sector` present) | `bsd4.cell` sector=mining.diamonds | 30 (20 amount ¢ + 10 count) |
| 21 | (iii) Gold | mapped (input_required until `sector` present) | `bsd4.cell` sector=mining.gold | 30 (20 amount ¢ + 10 count) |
| 22 | (iv) Manganese | mapped (input_required until `sector` present) | `bsd4.cell` sector=mining.manganese | 30 (20 amount ¢ + 10 count) |
| 23 | (v)  Quarrying | mapped (input_required until `sector` present) | `bsd4.cell` sector=mining.quarrying | 30 (20 amount ¢ + 10 count) |
| 24 | (vi) Other Mining Activity | mapped (input_required until `sector` present) | `bsd4.cell` sector=mining.other | 30 (20 amount ¢ + 10 count) |
| 28 | (i)  Food, Drink & Tobacco | mapped (input_required until `sector` present) | `bsd4.cell` sector=manufacturing.export.food_drink_tobacco | 30 (20 amount ¢ + 10 count) |
| 29 | (ii) Textiles, Clothing & Footwear | mapped (input_required until `sector` present) | `bsd4.cell` sector=manufacturing.export.textiles_clothing_footwear | 30 (20 amount ¢ + 10 count) |
| 30 | (iii) Sawmilling & Wood Processing | mapped (input_required until `sector` present) | `bsd4.cell` sector=manufacturing.export.sawmilling_wood_processing | 30 (20 amount ¢ + 10 count) |
| 31 | (iv)  Paper pulp & Paper products | mapped (input_required until `sector` present) | `bsd4.cell` sector=manufacturing.export.paper_pulp_products | 30 (20 amount ¢ + 10 count) |
| 32 | (v)  Chemicals and Fertilizers | mapped (input_required until `sector` present) | `bsd4.cell` sector=manufacturing.export.chemicals_fertilizers | 30 (20 amount ¢ + 10 count) |
| 33 | (vi) Iron and Steel | mapped (input_required until `sector` present) | `bsd4.cell` sector=manufacturing.export.iron_steel | 30 (20 amount ¢ + 10 count) |
| 34 | (vii) Boat/Ship Building and repairs | mapped (input_required until `sector` present) | `bsd4.cell` sector=manufacturing.export.boat_ship_building | 30 (20 amount ¢ + 10 count) |
| 35 | (viii) Manufacturing of Motor Vehicles | mapped (input_required until `sector` present) | `bsd4.cell` sector=manufacturing.export.motor_vehicles | 30 (20 amount ¢ + 10 count) |
| 36 | (ix) Other Unclassified | mapped (input_required until `sector` present) | `bsd4.cell` sector=manufacturing.export.other | 30 (20 amount ¢ + 10 count) |
| 38 | (i)  Food, Drink & Tobacco | mapped (input_required until `sector` present) | `bsd4.cell` sector=manufacturing.home.food_drink_tobacco | 30 (20 amount ¢ + 10 count) |
| 39 | (ii) Textiles, Clothing & Footwear | mapped (input_required until `sector` present) | `bsd4.cell` sector=manufacturing.home.textiles_clothing_footwear | 30 (20 amount ¢ + 10 count) |
| 40 | (iii) Sawmilling and Wood Processing | mapped (input_required until `sector` present) | `bsd4.cell` sector=manufacturing.home.sawmilling_wood_processing | 30 (20 amount ¢ + 10 count) |
| 41 | (iv) Paper,Pulp & Paper Products | mapped (input_required until `sector` present) | `bsd4.cell` sector=manufacturing.home.paper_pulp_products | 30 (20 amount ¢ + 10 count) |
| 42 | (v) Chemicals and Fertilizer | mapped (input_required until `sector` present) | `bsd4.cell` sector=manufacturing.home.chemicals_fertilizers | 30 (20 amount ¢ + 10 count) |
| 43 | (vi) Iron and Steel | mapped (input_required until `sector` present) | `bsd4.cell` sector=manufacturing.home.iron_steel | 30 (20 amount ¢ + 10 count) |
| 44 | (vii) Boat/Ship building and repairs | mapped (input_required until `sector` present) | `bsd4.cell` sector=manufacturing.home.boat_ship_building | 30 (20 amount ¢ + 10 count) |
| 45 | (viii) Manufacturing of Motor Vehicles | mapped (input_required until `sector` present) | `bsd4.cell` sector=manufacturing.home.motor_vehicles | 30 (20 amount ¢ + 10 count) |
| 46 | (ix)  Other Unclassified | mapped (input_required until `sector` present) | `bsd4.cell` sector=manufacturing.home.other | 30 (20 amount ¢ + 10 count) |
| 49 | (i)  Construction & Works | mapped (input_required until `sector` present) | `bsd4.cell` sector=construction.construction_works | 30 (20 amount ¢ + 10 count) |
| 50 | (ii)  Building Construction | mapped (input_required until `sector` present) | `bsd4.cell` sector=construction.building_construction | 30 (20 amount ¢ + 10 count) |
| 53 | (i) Electric light & Power | mapped (input_required until `sector` present) | `bsd4.cell` sector=utilities.electricity | 30 (20 amount ¢ + 10 count) |
| 54 | (ii) Gas Manufacture & Distribution | mapped (input_required until `sector` present) | `bsd4.cell` sector=utilities.gas | 30 (20 amount ¢ + 10 count) |
| 55 | (iii) Water Supply | mapped (input_required until `sector` present) | `bsd4.cell` sector=utilities.water | 30 (20 amount ¢ + 10 count) |
| 59 | (a) Motor Vehicle Import & Declaration | mapped (input_required until `sector` present) | `bsd4.cell` sector=commerce.import.motor_vehicles | 30 (20 amount ¢ + 10 count) |
| 60 | (b) Machinery & Heavy equipment | mapped (input_required until `sector` present) | `bsd4.cell` sector=commerce.import.machinery_heavy_equipment | 30 (20 amount ¢ + 10 count) |
| 61 | (c) Other Import Items | mapped (input_required until `sector` present) | `bsd4.cell` sector=commerce.import.other | 30 (20 amount ¢ + 10 count) |
| 64 | (a) Cocoa Exports | mapped (input_required until `sector` present) | `bsd4.cell` sector=commerce.export.cocoa | 30 (20 amount ¢ + 10 count) |
| 65 | (b) Timber Export | mapped (input_required until `sector` present) | `bsd4.cell` sector=commerce.export.timber | 30 (20 amount ¢ + 10 count) |
| 66 | (c) Other Export Items | mapped (input_required until `sector` present) | `bsd4.cell` sector=commerce.export.other | 30 (20 amount ¢ + 10 count) |
| 67 | (iii) Cocoa Marketing | mapped (input_required until `sector` present) | `bsd4.cell` sector=commerce.cocoa_marketing | 30 (20 amount ¢ + 10 count) |
| 68 | (iv) Timber Marketing | mapped (input_required until `sector` present) | `bsd4.cell` sector=commerce.timber_marketing | 30 (20 amount ¢ + 10 count) |
| 69 | (v)  Diamond Marketing | mapped (input_required until `sector` present) | `bsd4.cell` sector=commerce.diamond_marketing | 30 (20 amount ¢ + 10 count) |
| 70 | (vi) Mortgage Financing | mapped (input_required until `sector` present) | `bsd4.cell` sector=commerce.mortgage_financing | 30 (20 amount ¢ + 10 count) |
| 72 | (a) Hire Purchase Companies | mapped (input_required until `sector` present) | `bsd4.cell` sector=commerce.ofi.hire_purchase | 30 (20 amount ¢ + 10 count) |
| 73 | (b) Insurance Companies | mapped (input_required until `sector` present) | `bsd4.cell` sector=commerce.ofi.insurance | 30 (20 amount ¢ + 10 count) |
| 74 | (c) Building bodies and Corporations | mapped (input_required until `sector` present) | `bsd4.cell` sector=commerce.ofi.building_bodies | 30 (20 amount ¢ + 10 count) |
| 75 | (viii) Other Unclassified | mapped (input_required until `sector` present) | `bsd4.cell` sector=commerce.other | 30 (20 amount ¢ + 10 count) |
| 78 | (i)  Railway transport | mapped (input_required until `sector` present) | `bsd4.cell` sector=transport.railway | 30 (20 amount ¢ + 10 count) |
| 79 | (ii) Road transport | mapped (input_required until `sector` present) | `bsd4.cell` sector=transport.road | 30 (20 amount ¢ + 10 count) |
| 80 | (iii)Ocean and Other Water transport | mapped (input_required until `sector` present) | `bsd4.cell` sector=transport.water | 30 (20 amount ¢ + 10 count) |
| 81 | (iv) Air transport | mapped (input_required until `sector` present) | `bsd4.cell` sector=transport.air | 30 (20 amount ¢ + 10 count) |
| 82 | (v)  Storage and warehousing | mapped (input_required until `sector` present) | `bsd4.cell` sector=transport.storage_warehousing | 30 (20 amount ¢ + 10 count) |
| 83 | (vi) Communications | mapped (input_required until `sector` present) | `bsd4.cell` sector=transport.communications | 30 (20 amount ¢ + 10 count) |
| 86 | (I)  Printing, Publishing and Allied Products | mapped (input_required until `sector` present) | `bsd4.cell` sector=services.printing_publishing | 30 (20 amount ¢ + 10 count) |
| 87 | (ii) Business Services | mapped (input_required until `sector` present) | `bsd4.cell` sector=services.business | 30 (20 amount ¢ + 10 count) |
| 88 | (iii) Recreation Services | mapped (input_required until `sector` present) | `bsd4.cell` sector=services.recreation | 30 (20 amount ¢ + 10 count) |
| 89 | (iv) Personal Services | mapped (input_required until `sector` present) | `bsd4.cell` sector=services.personal | 30 (20 amount ¢ + 10 count) |
| 90 | (v) Salary Credit | mapped (input_required until `sector` present) | `bsd4.cell` sector=services.salary_credit | 30 (20 amount ¢ + 10 count) |
| 91 | (vi)  Other Services including Government Services | mapped (input_required until `sector` present) | `bsd4.cell` sector=services.other_incl_government | 30 (20 amount ¢ + 10 count) |
| 93 | 9.  MISCELLANEOUS | mapped (input_required until `sector` present) | `bsd4.cell` sector=miscellaneous | 30 (20 amount ¢ + 10 count) |

**Totals:** 63 leaf rows · 1,890 input cells bound (100%) · 0 unmapped · the group TOTAL columns, AP:AS, the 13 section/sub-section subtotal rows (9, 18, 26, 27, 37, 48, 52, 57, 58, 63, 71, 77, 85) and the GRAND TOTAL row are the template's own formulas evaluated over these inputs.

## Annexes — the SAME loan book re-cut (all loans, residents + non-residents)

The official annex sheets carry no `0` placeholders (blank grids), so their data cells are declared explicitly with `grid_lines`. Column C is the cedi amount (¢'Million at export); column D is the percentage of total loans (computed as C ÷ total × 100, `unscaled`) — the template leaves both blank for the bank to fill. Annex totals (`C15`, `C18`) are the template's own SUM formulas.

## Sheet `4a Annexure` — blank data grid (14 lines, 14 cells declared) · 1 template formula

| Row | Official line | Column C (¢) | Column D (% of total loans, unscaled) | Rule |
|---|---|---|---|---|
| 7 | Deposit-takers | `bsd4.annex4a` bucket=deposit_takers | `bsd4.annex4a` bucket=deposit_takers share | commercial banks + ODIs (Guide 4A note 2) |
| 8 | Central bank | `bsd4.annex4a` bucket=central_bank | `bsd4.annex4a` bucket=central_bank share | `CENTRAL_BANK` counterparties |
| 9 | Other Financial Corporation (OFCs) | `bsd4.annex4a` bucket=other_financial_corporations | `bsd4.annex4a` bucket=other_financial_corporations share | OFIs (Guide 4A note 3) |
| 10 | General Government | `bsd4.annex4a` bucket=general_government | `bsd4.annex4a` bucket=general_government share | central government + public institutions |
| 11 | Non-financial Corporations | `bsd4.annex4a` bucket=nonfinancial_corporations | `bsd4.annex4a` bucket=nonfinancial_corporations share | private corporations (foreign + indigenous) |
| 12 | Other domestic sectors | `bsd4.annex4a` bucket=other_domestic_sectors | `bsd4.annex4a` bucket=other_domestic_sectors share | households, public enterprises, NPISH and any other resident not covered (Guide 4A note 5) |
| 13 | Non-residents | `bsd4.annex4a` bucket=non_residents | `bsd4.annex4a` bucket=non_residents share | counterparty `resident=False` (Guide 4A note 6) |

Annex 4a total (`C15`) = Σ **all** LOAN positions with a counterparty; it therefore exceeds the main sheet's GRAND TOTAL by exactly the non-resident and unplaceable (CENTRAL_BANK) loans.

## Sheet `4b Annexure` — blank data grid (18 lines, 18 cells declared) · 1 template formula

| Row | Official line | Column C (¢) | Column D (% of total loans, unscaled) | Rule |
|---|---|---|---|---|
| 7 | Domestic Economy | `bsd4.annex4b` bucket=domestic | `bsd4.annex4b` bucket=domestic share | resident counterparty with country = bank jurisdiction or no country |
| 8 | Advanced economies | `bsd4.annex4b` bucket=advanced_economies | `bsd4.annex4b` bucket=advanced_economies share | ISO country ∈ IMF WEO advanced economies |
| 10 | Africa | `bsd4.annex4b` bucket=africa | `bsd4.annex4b` bucket=africa share | ISO country in Africa (SSA + North Africa) |
| 11 | - Of which: Sub-Saharan Africa* | `bsd4.annex4b` bucket=africa.sub_saharan | `bsd4.annex4b` bucket=africa.sub_saharan share | of which IMF WEO Sub-Saharan Africa (additional to *Africa*) |
| 12 | Asia | `bsd4.annex4b` bucket=asia | `bsd4.annex4b` bucket=asia share | emerging & developing Asia / Pacific |
| 13 | Europe | `bsd4.annex4b` bucket=europe | `bsd4.annex4b` bucket=europe share | emerging & developing Europe incl. FSU |
| 14 | - Of which: FSU, including Russia | `bsd4.annex4b` bucket=europe.fsu | `bsd4.annex4b` bucket=europe.fsu share | of which former Soviet Union incl. Russia (additional to *Europe*) |
| 15 | Middle East | `bsd4.annex4b` bucket=middle_east | `bsd4.annex4b` bucket=middle_east share | Middle East |
| 16 | Western Hemisphere | `bsd4.annex4b` bucket=western_hemisphere | `bsd4.annex4b` bucket=western_hemisphere share | Latin America & Caribbean |

Row 9 *Regions, excluding advanced economies* is a heading (blank, no formula) and is not bound. The template's `C18 = SUM(C7:C16)` **also adds the two "of which" rows** (11, 14) — BoG's arithmetic is reproduced as-is; the D-column percentages are computed against total loans (Σ of the top-level rows 7, 8, 10, 12, 13, 15, 16), not against C18.

## Residual — data the bank must supply / loans the map cannot place

- **`sector` attribute** on LOAN counterparties (or per facility on the position): without it the whole main sheet is `input_required`. This is the single data item BSD4 needs beyond the balance-sheet book; the T24 adapter already carries the core's industry code (`industry_code`) in counterparty attributes — the bank maps that code table to the keys above.
- **`borrower_class`** on `GOVERNMENT_ENTITY` counterparties (`public_institution` / `public_enterprise`): without it such loans are placed in **no** column (BSD2 §8 has the same gap) — the GRAND TOTAL then understates by that amount. Supply the attribute or set `borrower_class` explicitly.
- **`ownership=foreign`** on foreign-controlled `CORPORATE`/`SME` counterparties: absent it, all private corporations report as INDIGENOUS.
- **`institution_class`** on `NBFI` counterparties: absent it, an NBFI reports as OTHER FINANCIAL INSTITUTIONS (rural banks, S&Ls, credit unions, discount houses need the class to reach OTHER DEPOSITORY INSTITUTIONS).
- **NPISH** needs `borrower_class=npish` (no platform counterparty type expresses it); a bare `OTHER` counterparty is not placed.
- Loans to `CENTRAL_BANK` counterparties, `OTHER` without `borrower_class`, and LOAN positions with **no counterparty** cannot be placed in a borrower-class column (they do appear on Annex 4a where their sector is determinable).
- Non-resident loans without a recognised ISO country code cannot be placed on Annex 4b.
- Performing/non-performing follows IFRS 9 stage 3; a book without staged loans reports everything as performing.

## Cross-form dependencies

- Guide: BSD4 GRAND TOTAL (`AR95`) should agree with BSD2 item 8 sub-total (loans, overdrafts and advances) — the catalogue declares `depends_on=("BSD2",)`; no cell links across workbooks (the template has none), the tie-out is a review check.
- Annex 4a / 4b re-cut the same LOAN book (in-form).

## Framework asks

- Formula cells whose precedents are all `unscaled` inputs (the No. of Cust. group subtotals `E9`, `I9`, …, the grand column `AS<row>` and `AS95`) are still divided by the sheet's ¢'Million divisor at export because `unscaled` is tracked per bound INPUT cell only. The exporter should propagate `unscaled` through the evaluator (a formula over only-unscaled precedents is unscaled).
- (Resolved during this wave) `grid_lines` for blank annex grids and per-cell `unscaled` honoured at export.


# Search evaluation

_Generated 2026-10-06 17:02 by `python -m eval.run_eval`. 26 queries in English, Tamil and Hindi (see `eval/queries.json`)._

## Summary

| Variant | P@5 | nDCG@10 | Constraints | Multilingual parity | Latency p50 / p90 |
|---|---|---|---|---|---|
| A. Dense only | 0.80 | 0.75 | 88% | 72% | 0.19s / 0.64s |
| B. Hybrid (dense + sparse) | 0.79 | 0.74 | 88% | 74% | 0.10s / 0.11s |
| C. Hybrid + rerank | 0.83 | 0.79 | 86% | 80% | 9.74s / 10.82s |
| D. Full system (LLM + rules) | 1.00 | 0.94 | 100% | 100% | 12.42s / 15.50s |

- **P@5**: share of the top 5 results that are relevant (right category).
- **nDCG@10**: ranking quality; 1.0 = ten perfect results (right category and attribute) in order.
- **Constraints**: results obeying an explicit price or gender limit (items with unknown price/gender skipped).
- **Multilingual parity**: Tamil/Hindi P@5 as a share of the same query's English P@5.

### P@5 by query language

| Variant | en | hi | ta |
|---|---|---|---|
| A. Dense only | 0.87 | 0.60 | 0.70 |
| B. Hybrid (dense + sparse) | 0.84 | 0.60 | 0.70 |
| C. Hybrid + rerank | 0.89 | 0.75 | 0.65 |
| D. Full system (LLM + rules) | 1.00 | 1.00 | 1.00 |

### Full system details

- LLM parse success rate: **100%** (the rest fell back to plain hybrid search)
- Median time per stage: LLM parse 2418 ms (0 when cached), embedding 262 ms, search 24 ms, rerank 9386 ms

## Per query (P@5 for each variant)

| Query | Lang | A | B | C | D |
|---|---|---|---|---|---|
| formal business outfit | en | 0.80 | 0.60 | 0.80 | 1.00 |
| warm winter jacket | en | 1.00 | 1.00 | 1.00 | 1.00 |
| सर्दियों के लिए गर्म जैकेट | hi | 0.80 | 0.80 | 1.00 | 1.00 |
| குளிர்காலத்திற்கு சூடான ஜாக்கெட் | ta | 1.00 | 1.00 | 1.00 | 1.00 |
| outfit for the beach this summer | en | 1.00 | 0.80 | 1.00 | 1.00 |
| கோடை கடற்கரைக்கு ஏற்ற ஆடை | ta | 1.00 | 1.00 | 0.60 | 1.00 |
| गर्मियों में समुद्र तट के लिए कपड़े | hi | 1.00 | 1.00 | 1.00 | 1.00 |
| running shoes for women | en | 0.40 | 0.60 | 0.20 | 1.00 |
| महिलाओं के लिए दौड़ने के जूते | hi | 0.20 | 0.20 | 0.20 | 1.00 |
| பெண்கள் ஓடுவதற்கான காலணிகள் | ta | 0.00 | 0.00 | 0.00 | 1.00 |
| men's wedding outfit | en | 0.60 | 0.40 | 1.00 | 1.00 |
| शादी के लिए पुरुषों का आउटफिट | hi | 0.40 | 0.40 | 0.80 | 1.00 |
| women's swimsuit | en | 1.00 | 1.00 | 1.00 | 1.00 |
| பெண்களுக்கான நீச்சல் உடை | ta | 0.80 | 0.80 | 1.00 | 1.00 |
| men's leather wallet | en | 1.00 | 1.00 | 1.00 | 1.00 |
| women's sterling silver earrings | en | 1.00 | 1.00 | 1.00 | 1.00 |
| comfortable shoes for standing all day at work | en | 0.80 | 0.80 | 0.60 | 1.00 |
| yoga pants for women under $30 | en | 1.00 | 1.00 | 1.00 | 1.00 |
| kids rain boots | en | 0.20 | 0.20 | 0.40 | 1.00 |
| baseball cap for men | en | 0.80 | 0.80 | 1.00 | 1.00 |
| polarized sunglasses for driving | en | 1.00 | 1.00 | 1.00 | 1.00 |
| black cocktail dress for a party | en | 1.00 | 1.00 | 1.00 | 1.00 |
| cotton socks multipack | en | 1.00 | 1.00 | 1.00 | 1.00 |
| men's watch with leather strap | en | 1.00 | 1.00 | 1.00 | 1.00 |
| gym t-shirt for men under $25 | en | 1.00 | 1.00 | 1.00 | 1.00 |
| winter gloves for cold weather | en | 1.00 | 1.00 | 1.00 | 1.00 |

## Full system: top 3 results per query

**formal business outfit** (P@5 1.00): understood as *formal business outfit*
- [2] Women's Elegant Business Two Piece Office Lady Suit Set Work Blazer Pant (Suit Set-Light G (suits_formalwear)
- [2] SLIM-SATION Women's Wide Band Ankle Pant Pull-on Pant with Tummy Control (bottoms)
- [2] Shoes for Crews Men's Cambridge Sneaker (footwear)

**warm winter jacket** (P@5 1.00): understood as *warm winter jacket*
- [2] Keevoom Men's Waterproof Ski Jacket Winter Warm Snow Coat Windproof Mountain Raincoat Snow (outerwear)
- [2] Uoiuxc Women's Hooded Winter Coat Warm Fleeced Lined Parka Long Jackets (outerwear)
- [2] MAGCOMSEN Men's Winter Coats Water Resistant Snow Ski Jacket Fleece Lined Parka 4 Pockets (outerwear)

**सर्दियों के लिए गर्म जैकेट** (P@5 1.00): understood as *warm jacket for winter*
- [2] Keevoom Men's Waterproof Ski Jacket Winter Warm Snow Coat Windproof Mountain Raincoat Snow (outerwear)
- [2] Uoiuxc Women's Hooded Winter Coat Warm Fleeced Lined Parka Long Jackets (outerwear)
- [2] MAGCOMSEN Men's Winter Coats Water Resistant Snow Ski Jacket Fleece Lined Parka 4 Pockets (outerwear)

**குளிர்காலத்திற்கு சூடான ஜாக்கெட்** (P@5 1.00): understood as *warm jacket for winter*
- [2] Keevoom Men's Waterproof Ski Jacket Winter Warm Snow Coat Windproof Mountain Raincoat Snow (outerwear)
- [2] Uoiuxc Women's Hooded Winter Coat Warm Fleeced Lined Parka Long Jackets (outerwear)
- [2] MAGCOMSEN Men's Winter Coats Water Resistant Snow Ski Jacket Fleece Lined Parka 4 Pockets (outerwear)

**outfit for the beach this summer** (P@5 1.00): understood as *outfit for the beach this summer*
- [2] 2017 Women's Spring Summer Floral Print Striped Short Sleeve T Shirts Loose Casual Tops,Gr (tops)
- [2] atika Women's Casual Harem Shorts, Premium Ultra Buttery Soft Shorts, Elastic High Waisted (bottoms)
- [2] JEWSUN Barefoot Sandals with Rhinestones and Beads. Beach Wedding Barefoot Sandals, Beaded (footwear)

**கோடை கடற்கரைக்கு ஏற்ற ஆடை** (P@5 1.00): understood as *outfit suitable for summer beach*
- [2] 2017 Women's Spring Summer Floral Print Striped Short Sleeve T Shirts Loose Casual Tops,Gr (tops)
- [2] atika Women's Casual Harem Shorts, Premium Ultra Buttery Soft Shorts, Elastic High Waisted (bottoms)
- [2] KIDDAD Saltwater Heals Everything Shirt Women Hawaiian Palm Trees Graphic T-Shirt Summer V (tops)

**गर्मियों में समुद्र तट के लिए कपड़े** (P@5 1.00): understood as *clothes for beach in summer*
- [2] KIDDAD Saltwater Heals Everything Shirt Women Hawaiian Palm Trees Graphic T-Shirt Summer V (tops)
- [2] atika Women's Casual Harem Shorts, Premium Ultra Buttery Soft Shorts, Elastic High Waisted (bottoms)
- [2] 2017 Women's Spring Summer Floral Print Striped Short Sleeve T Shirts Loose Casual Tops,Gr (tops)

**running shoes for women** (P@5 1.00): understood as *running shoes for women*
- [2] PUMA Women's Muse Sneaker (footwear)
- [1] Hemlock Women High Heels Boots Plus Snow Calf Boots Wedge Heel Ankle Shoes Height Increase (footwear)
- [1] Nike Women's Basketball Shoes (footwear)

**महिलाओं के लिए दौड़ने के जूते** (P@5 1.00): understood as *running shoes for women*
- [2] PUMA Women's Muse Sneaker (footwear)
- [1] Hemlock Women High Heels Boots Plus Snow Calf Boots Wedge Heel Ankle Shoes Height Increase (footwear)
- [1] Nike Women's Basketball Shoes (footwear)

**பெண்கள் ஓடுவதற்கான காலணிகள்** (P@5 1.00): understood as *women's running shoes*
- [2] PUMA Women's Muse Sneaker (footwear)
- [1] Nike Women's Basketball Shoes (footwear)
- [2] Gravity Defyer Proven Pain Relief Women's G-Defy Ion Athletic Shoes for Knee Pain (footwear)

**men's wedding outfit** (P@5 1.00): understood as *men's wedding outfit*
- [2] Men Classic Black 2 Pcs Cuff links and 4pcs Cuff Studs for Fashion Luxurious Tuxedo Formal (suits_formalwear)
- [1] Helikon Men's SFU Next Trousers Coyote Ripstop (bottoms)
- [2] Shoes for Crews Men's Cambridge Sneaker (footwear)

**शादी के लिए पुरुषों का आउटफिट** (P@5 1.00): understood as *outfit for a wedding for men*
- [2] Men Classic Black 2 Pcs Cuff links and 4pcs Cuff Studs for Fashion Luxurious Tuxedo Formal (suits_formalwear)
- [1] Helikon Men's SFU Next Trousers Coyote Ripstop (bottoms)
- [2] Shoes for Crews Men's Cambridge Sneaker (footwear)

**women's swimsuit** (P@5 1.00): understood as *women's swimsuit*
- [2] Avidqueen Women's Sexy Lace Crochet Swimsuit Bikini Cover Up Beach Dress (White) (swimwear)
- [2] Women Crochet Bikini Set Knit 2PCS Bathing Suit Swimsuit Beachwear (swimwear)
- [2] COCOSHIP Navy Blue 1950s Retro Vintage One Piece Monokini White Anchors Swimsuits Swimwear (swimwear)

**பெண்களுக்கான நீச்சல் உடை** (P@5 1.00): understood as *swimsuit for women*
- [2] Avidqueen Women's Sexy Lace Crochet Swimsuit Bikini Cover Up Beach Dress (White) (swimwear)
- [2] Women Crochet Bikini Set Knit 2PCS Bathing Suit Swimsuit Beachwear (swimwear)
- [2] COCOSHIP Navy Blue 1950s Retro Vintage One Piece Monokini White Anchors Swimsuits Swimwear (swimwear)

**men's leather wallet** (P@5 1.00): understood as *men's leather wallet*
- [2] Genuine Leather Belts For Men, 100% Full Grain Mens Belt For Casual Wear, With Antique All (accessories)
- [2] Belt for Men -Trimmed to Fit- Top Class Genuine Leather Men's Belt (45-48, Brown) (accessories)
- [2] Men's Leather Belt 39"-70" Waist Regular and Big & Tall Sizes,Black & Brown Colors (48"-55 (accessories)

**women's sterling silver earrings** (P@5 1.00): understood as *women's sterling silver earrings*
- [2] INSPIRED BY YOU. Women's Earrings - 925 Sterling Silver Emerald Cut Ombre Crystal Stud Ear (jewelry)
- [2] Women’s 925 Sterling Silver Butterfly Wing Dangle Hook Earrings, 0.78” x 1.29” (jewelry)
- [2] MBLife 925 Sterling Silver Polish Finishing Diamond-Cut Huggie Hoop Earrings (0.6") (jewelry)

**comfortable shoes for standing all day at work** (P@5 1.00): understood as *comfortable shoes for standing all day at work*
- [2] OrthoComfoot Men's Loafers & Slip-ons,Plantar Fasciitis, Foot and Heel Pain Relief,Orthope (footwear)
- [2] OrthoComfoot Men's Plantar Fasciitis Slip-Ons Sneakers, Arch Support Walking Loafers, Foot (footwear)
- [2] MEGNYA Orthotic Sandals for Women, Plantar Fasciitis Sandals for Flat Feet, Orthopedic Wal (footwear)

**yoga pants for women under $30** (P@5 1.00): understood as *yoga pants for women under $30*
- [2] Neonysweets Women Sports Yoga Workout Leggings Hidden Pocket (bottoms)
- [2] Aoxjox Women's High Waist Workout Gym Vital Seamless Leggings Yoga Pants (bottoms)
- [2] Oalka Women's Joggers High Waist Yoga Pockets Sweatpants Sport Workout Pants (bottoms)

**kids rain boots** (P@5 1.00): understood as *kids rain boots*
- [2] Toddler & Little Girls Youth Pink Polka Dot Rain Snow Boots w/Great Lining, Comfortable (1 (footwear)
- [2] KDHAO Baby Kids Comfortable Casual Shoes Winter Girls Boys Lovely Hiking Snow Boots(Todder (footwear)
- [2] QGAKAGO(TM Baby Multicolor Rainbow Cotton Knit Premium Soft Sole Anti-Slip Warm Winter Inf (footwear)

**baseball cap for men** (P@5 1.00): understood as *baseball cap for men*
- [2] AMERICAN NEEDLE Fender Guitars Officially Licensed Music Hat OSFA Adjustable New (headwear)
- [2] DisplayGifts Pro UV Protection Baseball Cap Hat Display Case Holder Stand Perfect for Base (headwear)
- [2] Lsinyan New Hot Deep Blue Fashion Baseball Snapback Hats and Caps for Men Cool Cotton Adju (headwear)

**polarized sunglasses for driving** (P@5 1.00): understood as *polarized sunglasses for driving*
- [2] Premium Aviator Polarized Sunglasses Men Women Metal Frame Sun Mirror Glasses for Driving (eyewear)
- [2] Men's Women's UV400 Polarized Driving Sports Metal Frame Sunglasses (eyewear)
- [2] Ewin E31 Polarized Sports Sunglasses with Case for Men Women Baseball Fishing Golf Driving (eyewear)

**black cocktail dress for a party** (P@5 1.00): understood as *black cocktail dress for a party*
- [2] Kearia Women Short Sleeve Deep V-Neck Sequin Split Bodycon Cocktail Party Dress Black XLar (dresses)
- [2] Zalalus Women's Lace Dresses for Cocktail Wedding Party Elegant High Neck Short Sleeves Ab (dresses)
- [2] VETIOR Women's Vintage Scoop Neck Midi Dress Sleeveless A-line Cocktail Party Dress Large  (dresses)

**cotton socks multipack** (P@5 1.00): understood as *cotton socks multipack*
- [2] Bienvenu lady's 4 Pack Pattern Warm Cotton Crew Socks,Multicolor 3 (underwear_socks)
- [2] YUEDGE Women's 3Pack Multi Performance Outdoor Hiking Trekking Cushion Crew Socks, L(Women (underwear_socks)
- [2] Pack of 6 Mens Dress Socks,Cotton Socks,Crew Socks,Classic Comfortable Soft Socks For Men  (underwear_socks)

**men's watch with leather strap** (P@5 1.00): understood as *men's watch with leather strap*
- [2] Kenneth Cole New York Men's 'Transparency' Quartz Stainless Steel and Leather Casual Watch (watches)
- [2] Brown Leather Strap Watches for Men - Easy to Reader Week and Date Waterproof Business Dre (watches)
- [2] Nixon Men's Quartz Watch The Ride Brown / Black A315562-00 with Leather Strap (watches)

**gym t-shirt for men under $25** (P@5 1.00): understood as *gym t-shirt for men under $25*
- [2] SPORT-TEK Men's PosiCharge Competitor Tee (tops)
- [2] ZUEVI Muscle Tank Tops for Men Cut Open Sides Bodybuilding Vest Gym Workout Stringer T-Shi (tops)
- [2] Aiyino Men's Short Sleeve Athletic T-Shirt Classic Top Casual Workout Sports Summer Shirts (tops)

**winter gloves for cold weather** (P@5 1.00): understood as *winter gloves for cold weather*
- [2] SKYDEER 3M Thinsulate Thermal Winter Work Gloves with Windproof Premium Genuine Deerskin S (accessories)
- [2] Womens Leather Winter Gloves - Touchscreen Deerskin Suede Thermal Cashmere Adjustable Wris (accessories)
- [2] Dimore 3 Pairs Winter Gloves for Women Cold Weather Girls With Touch Screen Fingers Warm T (accessories)

## Limitations

- Relevance is judged by rules (category + keywords/tags), not by people; style and taste aren't measured.
- 26 queries is a small sample: good for comparing variants, not a precise benchmark.
- Product categories come from title rules and a small LLM, so a few 'wrong category' results may be label errors.

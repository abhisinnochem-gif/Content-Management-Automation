Catalog Automation – Weekly Update ( 22nd March ) 
1.	HTML Template Optimization
Actions on 13th March - Abhi to run full testing and finalize templates
TEST 1 : Test the HTML code on mobile devices to see the layout and check if its mobile friendly layout . 
This product SKU: 69161 
•	STEP 8: Images collection and resizing is now been done by automation script. This only covers vendor that with SKU. We have covered approx. 5000 products and 24 vendors. Statistically this covers 1% products in our database and about 15% of the vendors. 
•	Out of 24 vendors – 2 vendor webistes the script was not able to crawl and was facing error #403. We need to figure out a stagegy for this and we are currently working on this.
•	For the remaining not SKU based products and vendor – Worked on 5 different vendors and prepared a script for non SKU based vendor. The scipt crawls based on UPC and product name – In this case there are two variation we are observing 
o	Case 1 : Vendors who have upc in their database . We tested this with 3-4 vendor and we were able to fetch images for 1 vendor susscefully 
o	Case 2 : Vendors who are using internal SKU and we are not receving from the distributor . We have issues fetching the images because the script cannot pick the right image because we don’t have the vendor website SKU for the products.
o	Case 3 : Small time vendors who may have UPC but don’t associate with the upc on their website and need to find the product purely based on product name. However this is very low quality , each vendor can have variation in the name Eg: Different spellings , abravated names , trimmed names and naming convention names.( Turmeric powder Vcaps/ Turmeic powder Veg capsules ) and may also include 100 mg or serving sizes btw the name itself.


•	Table generation for the ingredient table is done by cladue AI. We are having an issue with table generation logic in the script directly cause it needs Athropic API key which needs a credit card or we can generate it directly on the claude AI app directly as we are doing it right now . This covers STEP 7 partially where we are automating the Automation of generating supplement fact table HTML code.
•	Another part of STEP 7 is description generation thru the script . Right now we can generate description thru claude Ai app and are still facing the same issue as above with API key. 
•	
https://www.herbspro.com/products/pepzin-gi-69161

Catalog Automation – Weekly Update ( 13th March )

1. HTML Template Optimization
	•	Core HTML templates have been mobile optimized.
	•	Font, borders, and layout structure finalized.
	•	Testing currently underway across multiple vendors.
	•	Plan to validate using 100 products across 20 vendors to ensure consistency across categories (supplements, beauty, etc.).

Action Items
	•	Sri Lakshmi to provide 100 product test list (20 vendors × 5 products each).
	•	Abhi to run full testing and finalize templates.

⸻

2. Image Scraping & Resizing Automation
	•	Automation scripts developed to collect and resize high-quality vendor images.
	•	Currently operational for ~14–16 vendors.
	•	Works for products with UPC + SKU identifiers.

Coverage
	•	Vendors with UPC/SKU represent ~15% of catalog (~5,000 products).

Impact
Manual processing per product:
	•	Image collection: 3–4 minutes
	•	HTML tables + formatting: 15–20 minutes

Automation reduces this to seconds, saving thousands of manual hours.

⸻

3. Script Architecture

Current system uses:
	•	Core automation script
	•	Vendor-specific plugins for different websites
	•	Excel input sheet (UPC, SKU, vendor URL, product name)

Script automatically:
	1.	Identifies vendor
	2.	Scrapes images
	3.	Resizes images
	4.	Saves images indexed by UPC

⸻

4. Next Phase (Next Week)

Goals for the coming week:
	•	Extend automation to all vendors with UPC/SKU (~24 vendors).
	•	Evaluate combining parsing + HTML generation + image collection into a single pipeline.
	•	Assess Anthropic API usage for automated parsing and formatting.

Team will provide cost estimates for API usage before implementation.

⸻

Next step:
	•	Engineering to deploy a test instance and provide credentials for UAT testing.

⸻

6. Summary
	•	Automation currently covers ~15% of catalog (~5,000 products).
	•	Significant reduction in manual catalog processing time.

# Valuebetennis source acceptance follow-up, 28 November 2026

Official listing https://www.valuebetennis.com/en/donnees.htm now reports **480,102** settled singles, updated 28 November (100 additional 2026 rows versus previous listing); 2026 82,845. Earlier years unchanged. Published CC BY 4.0; no independent rights certification.

Methodology https://www.valuebetennis.com/en/methodologie.htm explicitly lists tennis-data.co.uk, Jeff Sackmann (noncommercial licence in its source list), ATP and WTA rankings and Pinnacle. It says post-2020 matches are live-imported, but does not establish field-by-field independence of the downloadable files from these sources. This is a concrete reason to keep clean-candidate commercial activation blocked and ask for source-specific chain of rights. The downloadable schema provides date, identifiers, category, gender, round, score and odds; no individual capture/ingestion timestamp is documented. Scheduled time must not be mistaken for actual competitive start or result availability.

Both the 2021 and 2026 public download routes returned unsupported CSV content through the available browser retrieval; direct 2026 download failed. No file bytes inspected, no actual usable counts. An aggregate-only local audit now supports the documented columns, multiple season files, exact file hashes, duplicate IDs and conflicts, competition/gender groups, missing results and fields, date precision, midnight flags and player name variants. Run locally with the downloaded authorized inspection files:

    python ops/validation/valuebetennis_export_audit_20261128.py /secure/valuebetennis-matchs-*.csv

Structural completeness is not point-in-time completeness, cross-provider identity, or commercial clearance. No model training or production import. Supplier enquiry remains unsent pending company sender confirmation. Existing frozen A1 candidates unchanged.

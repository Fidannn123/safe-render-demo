"""Static catalog of the 8 SYNTHETIC SMEs. Names are invented; any resemblance to a real business is
coincidental. Amounts are integer USD minor units (cents). The seed varies amounts, dates, accounts,
references and scenario details; this catalog fixes only the shape of each business.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Stress = Literal["NONE", "LOW_BUFFER", "SHORTFALL"]


@dataclass(frozen=True)
class VendorSpec:
    name: str
    expense_account: Literal["Expense", "COGS"]
    cadence_days: int  # 7 or 14
    terms_days: int  # 14 or 30
    base_minor: int
    criticality: int  # 1 low .. 3 critical (never deferred)
    late_fee_minor: int  # flat late fee printed on invoices


@dataclass(frozen=True)
class CustomerSpec:
    name: str
    cadence_days: int
    terms_days: int
    weight: int  # share of revenue
    on_time_pct: int  # probability (percent) an invoice is paid on or before due date


@dataclass(frozen=True)
class SectorSpec:
    key: str
    sector: str
    company: str
    country: str
    vendors: tuple[VendorSpec, ...]  # 6 regulars then 3 extras (role-assigned per seed)
    big_vendor: VendorSpec | None  # stressed twins: source of the large scheduled payable
    customers: tuple[CustomerSpec, ...]
    payroll_weekly_minor: int
    bundle: tuple[tuple[str, int], ...]  # 28-day direct debits (payee, amount), all on one day
    revenue_factor_pct: int  # weekly revenue as % of weekly costs
    stress: Stress


def _v(  # noqa: PLR0917
    name: str, acct: Literal["Expense", "COGS"], cad: int, terms: int, base: int, crit: int, fee: int
) -> VendorSpec:
    return VendorSpec(name, acct, cad, terms, base, crit, fee)


def _c(name: str, cad: int, terms: int, weight: int, on_time: int) -> CustomerSpec:
    return CustomerSpec(name, cad, terms, weight, on_time)


SECTORS: tuple[SectorSpec, ...] = (
    SectorSpec(
        key="retail",
        sector="Retail",
        company="Northfield Corner Store Ltd",
        country="GB",
        vendors=(
            _v("Harbour Wholesale Foods Ltd", "COGS", 7, 14, 260000, 3, 5000),
            _v("Crestline Beverages Ltd", "COGS", 7, 14, 120000, 2, 2500),
            _v("Pinewood Household Goods", "COGS", 14, 30, 180000, 2, 4000),
            _v("Brightpack Packaging Co", "Expense", 14, 30, 45000, 1, 1500),
            _v("Kestrel Cleaning Services", "Expense", 7, 14, 30000, 1, 1000),
            _v("Ashgrove Dairy Farmers", "COGS", 7, 14, 95000, 3, 2500),
            _v("Meridian Shelf Fittings", "Expense", 14, 30, 60000, 1, 1500),
            _v("Oakhurst Snacks Ltd", "COGS", 14, 30, 85000, 2, 2000),
            _v("Silverline Security Systems", "Expense", 14, 30, 40000, 1, 1000),
        ),
        big_vendor=None,
        customers=(
            _c("Hillside Primary School", 14, 30, 3, 90),
            _c("Riverbend Care Home", 7, 14, 4, 80),
            _c("Westgate Office Park", 14, 30, 2, 60),
            _c("Card settlements (daily batch)", 7, 14, 6, 100),
        ),
        payroll_weekly_minor=420000,
        bundle=(
            ("Northfield Property Lettings", 380000),
            ("Unity Power and Gas", 65000),
            ("Till Cloud POS Subscription", 12000),
            ("Shield Mutual Insurance", 30000),
        ),
        revenue_factor_pct=125,
        stress="NONE",
    ),
    SectorSpec(
        key="construction",
        sector="Construction",
        company="Granite Ridge Builders GmbH",
        country="DE",
        vendors=(
            _v("Basalt Aggregates GmbH", "COGS", 7, 14, 310000, 3, 8000),
            _v("Ironvale Steel Supply", "COGS", 14, 30, 340000, 3, 9000),
            _v("Timberline Lumber Yard", "COGS", 7, 14, 150000, 2, 4000),
            _v("Quickmix Concrete Delivery", "COGS", 7, 14, 120000, 3, 3000),
            _v("Safestep Scaffolding Hire", "Expense", 14, 30, 90000, 2, 2500),
            _v("Voltwise Electrical Wholesale", "COGS", 14, 30, 110000, 2, 3000),
            _v("Clearsite Waste Removal", "Expense", 14, 30, 50000, 1, 1500),
            _v("Hartmann Tool Rental", "Expense", 14, 30, 70000, 1, 2000),
            _v("Brightway Site Lighting", "Expense", 14, 30, 35000, 1, 1000),
        ),
        big_vendor=_v("Kraneworks Equipment Leasing", "Expense", 28, 30, 0, 2, 25000),
        customers=(
            _c("Lindenhof Housing Association", 14, 30, 5, 60),
            _c("Municipal Schools Department", 14, 30, 3, 90),
            _c("Bergmann Retail Park Developer", 14, 30, 3, 50),
        ),
        payroll_weekly_minor=900000,
        bundle=(
            ("Yard Lease Steinfeld", 300000),
            ("Stadtwerke Energy Supply", 80000),
            ("Fleet Telematics Subscription", 25000),
            ("Bauversicherung Mutual", 60000),
        ),
        revenue_factor_pct=104,
        stress="SHORTFALL",
    ),
    SectorSpec(
        key="restaurant",
        sector="Restaurant",
        company="Saffron Table Bistro BV",
        country="NL",
        vendors=(
            _v("Polder Fresh Produce BV", "COGS", 7, 14, 140000, 3, 3000),
            _v("Noordzee Fish Traders", "COGS", 7, 14, 110000, 3, 3000),
            _v("Vinoteca Wine Imports", "COGS", 14, 30, 210000, 2, 5000),
            _v("Linnen Laundry Service", "Expense", 7, 14, 25000, 1, 1000),
            _v("Kaasmeester Cheese Supply", "COGS", 14, 30, 60000, 2, 1500),
            _v("Spice Route Dry Goods", "COGS", 14, 30, 45000, 2, 1500),
            _v("Coldchain Refrigeration Repair", "Expense", 14, 30, 40000, 1, 1000),
            _v("Bakkerij De Molen", "COGS", 7, 14, 30000, 2, 1000),
            _v("Tablewise Crockery Supply", "Expense", 14, 30, 35000, 1, 1000),
        ),
        big_vendor=_v("Grachten Kitchen Refit BV", "Expense", 28, 30, 0, 1, 20000),
        customers=(
            _c("Card settlements (daily batch)", 7, 14, 8, 100),
            _c("Canalside Events Agency", 14, 30, 2, 50),
            _c("Hofstede Corporate Catering", 14, 30, 2, 70),
        ),
        payroll_weekly_minor=520000,
        bundle=(
            ("Herengracht Property Rent", 420000),
            ("Grachten Utility Co-op", 90000),
            ("ReserveTable Booking Software", 15000),
            ("Horeca Liability Insurance", 30000),
        ),
        revenue_factor_pct=106,
        stress="LOW_BUFFER",
    ),
    SectorSpec(
        key="logistics",
        sector="Logistics",
        company="Bluehaul Logistics GmbH",
        country="AT",
        vendors=(
            _v("Alpenfuel Diesel Cards", "COGS", 7, 14, 280000, 3, 7000),
            _v("Donau Tyre and Service", "Expense", 14, 30, 120000, 2, 3000),
            _v("Brenner Toll Services", "Expense", 7, 14, 90000, 3, 2500),
            _v("Palletpro Equipment", "COGS", 14, 30, 70000, 1, 2000),
            _v("Tauern Truck Repairs", "Expense", 14, 30, 150000, 2, 4000),
            _v("Routewise Telematics", "Expense", 14, 30, 40000, 1, 1000),
            _v("Inntal Warehouse Staffing", "Expense", 7, 14, 110000, 2, 3000),
            _v("Kufstein Cargo Insurance Brokers", "Expense", 14, 30, 50000, 1, 1500),
            _v("Salzach Forklift Hire", "Expense", 14, 30, 45000, 1, 1500),
        ),
        big_vendor=None,
        customers=(
            _c("Ostmark Retail Distribution", 7, 14, 5, 90),
            _c("Lakeside Furniture Makers", 14, 30, 2, 70),
            _c("Vienna Pharma Wholesale", 14, 30, 3, 100),
            _c("Graz Electronics Outlet", 14, 30, 2, 60),
        ),
        payroll_weekly_minor=780000,
        bundle=(
            ("Depot Lease Wels", 450000),
            ("Netzwerk Power Supply", 70000),
            ("Dispatch Cloud Subscription", 20000),
            ("Fleet Insurance Mutual", 90000),
        ),
        revenue_factor_pct=122,
        stress="NONE",
    ),
    SectorSpec(
        key="pharmacy",
        sector="Pharmacy",
        company="Greenleaf Community Pharmacy Ltd",
        country="GB",
        vendors=(
            _v("Medway Pharmaceutical Wholesale", "COGS", 7, 14, 330000, 3, 8000),
            _v("Caretrail Medical Supplies", "COGS", 14, 30, 120000, 2, 3000),
            _v("Vitalis Health Products", "COGS", 14, 30, 80000, 2, 2000),
            _v("Coldbox Vaccine Logistics", "Expense", 14, 30, 60000, 3, 1500),
            _v("Dispensary Systems Ltd", "Expense", 14, 30, 45000, 2, 1500),
            _v("Thamesford Cosmetics", "COGS", 14, 30, 70000, 1, 2000),
            _v("Clearview Optical Supply", "COGS", 14, 30, 50000, 1, 1500),
            _v("Purewell Cleaning Contractors", "Expense", 7, 14, 20000, 1, 1000),
            _v("Elmstead Courier Services", "Expense", 7, 14, 25000, 1, 1000),
        ),
        big_vendor=None,
        customers=(
            _c("Regional Health Board Reimbursements", 14, 30, 6, 100),
            _c("Card settlements (daily batch)", 7, 14, 5, 100),
            _c("Oakwood Nursing Home", 14, 30, 2, 70),
        ),
        payroll_weekly_minor=510000,
        bundle=(
            ("High Street Estates Rent", 330000),
            ("Brightgrid Energy", 55000),
            ("PMR Software Licence", 18000),
            ("Pharmacist Indemnity Insurance", 40000),
        ),
        revenue_factor_pct=124,
        stress="NONE",
    ),
    SectorSpec(
        key="agriculture",
        sector="Agriculture",
        company="Meadowbrook Farm Cooperative eG",
        country="DE",
        vendors=(
            _v("Feldkraft Seed and Fertiliser", "COGS", 14, 30, 360000, 3, 9000),
            _v("Agrodiesel Nord", "COGS", 7, 14, 160000, 3, 4000),
            _v("Weser Veterinary Practice", "Expense", 14, 30, 70000, 2, 2000),
            _v("Heuwagen Feed Mill", "COGS", 7, 14, 190000, 3, 5000),
            _v("Traktor Service Hansen", "Expense", 14, 30, 90000, 2, 2500),
            _v("Irrigation Parts Direct", "COGS", 14, 30, 40000, 1, 1000),
            _v("Sonnenhof Seasonal Labour", "Expense", 7, 14, 120000, 2, 3000),
            _v("Kornspeicher Grain Storage", "Expense", 14, 30, 60000, 1, 1500),
            _v("Elbtal Crop Insurance Brokers", "Expense", 14, 30, 45000, 1, 1500),
        ),
        big_vendor=_v("Landtechnik Harvester Finance", "Expense", 28, 30, 0, 2, 30000),
        customers=(
            _c("Regional Dairy Collective", 14, 30, 4, 80),
            _c("Frischmarkt Grocery Chain", 14, 30, 4, 60),
            _c("Biogas Plant Uelzen", 14, 30, 1, 90),
        ),
        payroll_weekly_minor=600000,
        bundle=(
            ("Pachthof Land Lease", 350000),
            ("Agrarstrom Energy", 95000),
            ("Herd Management Software", 12000),
            ("Hagel Mutual Insurance", 70000),
        ),
        revenue_factor_pct=103,
        stress="SHORTFALL",
    ),
    SectorSpec(
        key="it_services",
        sector="IT services",
        company="Cobalt Byte IT Services BV",
        country="NL",
        vendors=(
            _v("Delta Hosting Datacentre BV", "COGS", 14, 30, 240000, 3, 6000),
            _v("Polderlink Fibre Connectivity", "COGS", 14, 30, 90000, 3, 2500),
            _v("Contractor Collective Utrecht", "COGS", 7, 14, 300000, 2, 7000),
            _v("Laptop Leasing Benelux", "Expense", 14, 30, 110000, 2, 3000),
            _v("Securescan Pen Testing", "COGS", 14, 30, 150000, 1, 4000),
            _v("Workspace Coffee Service", "Expense", 7, 14, 15000, 1, 500),
            _v("Licentie Software Reseller", "COGS", 14, 30, 130000, 2, 3500),
            _v("Amstel Training Academy", "Expense", 14, 30, 60000, 1, 1500),
            _v("Zuidas Recruitment Partners", "Expense", 14, 30, 90000, 1, 2500),
        ),
        big_vendor=None,
        customers=(
            _c("Havenstad Logistics Group", 14, 30, 4, 90),
            _c("Tulpen Insurance Mutual", 14, 30, 3, 80),
            _c("Rijnmond Port Authority IT", 14, 30, 3, 100),
            _c("Startup Studio Eindhoven", 14, 14, 1, 40),
        ),
        payroll_weekly_minor=950000,
        bundle=(
            ("Canal Office Lease", 400000),
            ("Groen Energy Supply", 40000),
            ("DevTools Cloud Subscriptions", 60000),
            ("Cyber Liability Insurance", 50000),
        ),
        revenue_factor_pct=126,
        stress="NONE",
    ),
    SectorSpec(
        key="textiles",
        sector="Textiles",
        company="Loomcraft Textiles GmbH",
        country="AT",
        vendors=(
            _v("Wollhaus Yarn Spinners", "COGS", 7, 14, 290000, 3, 7000),
            _v("Farbwerk Dye Chemicals", "COGS", 14, 30, 130000, 3, 3500),
            _v("Nadel Machine Parts", "Expense", 14, 30, 70000, 2, 2000),
            _v("Baumwoll Import Linz", "COGS", 14, 30, 210000, 2, 5000),
            _v("Packwerk Cartons", "Expense", 14, 30, 40000, 1, 1000),
            _v("Spindel Maintenance Crew", "Expense", 7, 14, 60000, 2, 1500),
            _v("Donaukurier Freight", "Expense", 7, 14, 45000, 1, 1500),
            _v("Musterzimmer Design Studio", "Expense", 14, 30, 80000, 1, 2000),
            _v("Alpenlicht Lighting Repairs", "Expense", 14, 30, 30000, 1, 1000),
        ),
        big_vendor=None,
        customers=(
            _c("Modehaus Fashion Retail", 14, 30, 4, 80),
            _c("Hotel Linen Contracts Tirol", 14, 30, 3, 90),
            _c("Upholstery Works Bregenz", 14, 30, 2, 60),
            _c("Online Shop Settlements", 7, 14, 3, 100),
        ),
        payroll_weekly_minor=700000,
        bundle=(
            ("Mill Building Lease Steyr", 360000),
            ("Wasserkraft Power", 110000),
            ("ERP Cloud Licence", 20000),
            ("Industrial Property Insurance", 45000),
        ),
        revenue_factor_pct=123,
        stress="NONE",
    ),
)

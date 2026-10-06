"""Seed the demo catalog.

The rows are written out here, not imported from application code: a
migration is a frozen snapshot, and must produce the same result in a year
even if the app's own data files have changed by then.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-23
Author: Sanjay Naidu
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# (sku, name, category, description, price_cents, stock, requires_prescription)
PRODUCTS = [
    (
        "OTC-PAR-500",
        "Paracetamol 500 mg (24 tablets)",
        "Pain Relief",
        "Relief from mild to moderate pain and fever.",
        399,
        120,
        False,
    ),
    (
        "OTC-IBU-200",
        "Ibuprofen 200 mg (32 tablets)",
        "Pain Relief",
        "Anti-inflammatory pain relief for headaches, muscle and joint pain.",
        549,
        90,
        False,
    ),
    (
        "OTC-ASP-075",
        "Low-dose Aspirin 75 mg (28 tablets)",
        "Pain Relief",
        "Daily low-dose aspirin. Use only as advised by your doctor.",
        299,
        60,
        False,
    ),
    (
        "OTC-CET-010",
        "Cetirizine 10 mg (30 tablets)",
        "Allergy",
        "Once-daily, non-drowsy relief from hay fever and allergies.",
        699,
        80,
        False,
    ),
    (
        "OTC-LOR-010",
        "Loratadine 10 mg (30 tablets)",
        "Allergy",
        "24-hour antihistamine for seasonal allergies.",
        649,
        70,
        False,
    ),
    (
        "OTC-FLU-NS",
        "Saline Nasal Spray 20 ml",
        "Cold & Flu",
        "Drug-free spray to clear a blocked nose.",
        499,
        50,
        False,
    ),
    (
        "OTC-THR-LOZ",
        "Honey & Lemon Throat Lozenges (36)",
        "Cold & Flu",
        "Soothing lozenges for sore throats.",
        349,
        150,
        False,
    ),
    (
        "OTC-ORS-10",
        "Oral Rehydration Salts (10 sachets)",
        "Digestive",
        "Replaces fluids and salts lost through diarrhoea.",
        599,
        40,
        False,
    ),
    (
        "OTC-ANT-48",
        "Antacid Chewable Tablets (48)",
        "Digestive",
        "Fast relief from heartburn and indigestion.",
        449,
        75,
        False,
    ),
    (
        "VIT-D3-1000",
        "Vitamin D3 1000 IU (90 softgels)",
        "Vitamins",
        "Supports bone health and immune function.",
        899,
        100,
        False,
    ),
    (
        "VIT-MULTI-60",
        "Daily Multivitamin (60 tablets)",
        "Vitamins",
        "A-to-Z multivitamins and minerals for adults.",
        1199,
        65,
        False,
    ),
    (
        "FA-BAND-40",
        "Assorted Adhesive Bandages (40)",
        "First Aid",
        "Breathable fabric plasters in four sizes.",
        399,
        200,
        False,
    ),
    (
        "FA-ANTI-50",
        "Antiseptic Cream 50 g",
        "First Aid",
        "Cleans and protects minor cuts, grazes and burns.",
        529,
        55,
        False,
    ),
    (
        "RX-AMOX-500",
        "Amoxicillin 500 mg (21 capsules)",
        "Prescription",
        "Antibiotic. Prescription only: a pharmacist verifies every order.",
        1299,
        30,
        True,
    ),
    (
        "RX-ATOR-020",
        "Atorvastatin 20 mg (28 tablets)",
        "Prescription",
        "Cholesterol-lowering statin. Prescription only.",
        1549,
        40,
        True,
    ),
    (
        "RX-METF-500",
        "Metformin 500 mg (56 tablets)",
        "Prescription",
        "Type 2 diabetes treatment. Prescription only.",
        999,
        45,
        True,
    ),
    (
        "RX-LISI-010",
        "Lisinopril 10 mg (28 tablets)",
        "Prescription",
        "Blood pressure treatment. Prescription only.",
        1149,
        35,
        True,
    ),
    (
        "RX-SALB-100",
        "Salbutamol Inhaler 100 mcg",
        "Prescription",
        "Reliever inhaler for asthma. Prescription only.",
        1899,
        25,
        True,
    ),
]


def upgrade() -> None:
    products = sa.table(
        "products",
        sa.column("sku", sa.String),
        sa.column("name", sa.String),
        sa.column("category", sa.String),
        sa.column("description", sa.Text),
        sa.column("price_cents", sa.Integer),
        sa.column("stock", sa.Integer),
        sa.column("requires_prescription", sa.Boolean),
    )
    op.bulk_insert(
        products,
        [
            dict(
                zip(
                    (
                        "sku",
                        "name",
                        "category",
                        "description",
                        "price_cents",
                        "stock",
                        "requires_prescription",
                    ),
                    row,
                    strict=True,
                )
            )
            for row in PRODUCTS
        ],
    )


def downgrade() -> None:
    skus = ", ".join(f"'{row[0]}'" for row in PRODUCTS)
    op.execute(f"DELETE FROM products WHERE sku IN ({skus})")  # noqa: S608 - constant SKUs

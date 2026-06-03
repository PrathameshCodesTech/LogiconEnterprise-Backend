"""
apps/sales/survey_templates.py

Default Excel-aligned templates for `SiteSurvey` child tables, consumed by
`apps.sales.services.seed_default_survey_lines`.

Only structure/metadata lives here. Numeric inputs (counts, amounts, totals)
and free-text fields (value_text, remarks, improvement_details) are always
left empty/zero at seed time and become user data.
"""

# Section 1-5: scope answers (free-text fields grouped into categories).
# Order matters: sort_order is the index inside the category.
SCOPE_FIELDS = {
    'client_scope_site': [
        ('site_name', 'Site Name'),
        ('company_name', 'Company Name'),
        ('address', 'Address'),
        ('contact_person_at_site', 'Contact Person at Site'),
        ('contact_phone_fax_mobile', 'Phone / Fax / Mobile'),
        ('ops_maintenance_in_scope', 'Ops & Maintenance In-Scope'),
    ],
    'premises': [
        ('company_details_and_product', 'Company Details and Product'),
        ('area_details_sq_ft_approx', 'Area Details (Sq.Ft Approx)'),
        ('number_of_floors', 'Number of Floors'),
        ('gents_toilets_to_be_maintained_details',
         'Gents Toilets to be Maintained — Details'),
        ('floor_types', 'Floor Types'),
        ('ladies_toilets_to_be_maintained_details',
         'Ladies Toilets to be Maintained — Details'),
        ('number_of_basements', 'Number of Basements'),
        ('basements_used_for', 'Basements Used For'),
        ('electrical_room_ac_room_location',
         'Electrical Room / AC Room Location'),
        ('number_of_staircases', 'Number of Staircases'),
    ],
    'hk_info': [
        ('area_of_cleaning', 'Area of Cleaning'),
    ],
    'technical_scope': [
        ('supply_and_ht_details', 'Supply and HT Details'),
        ('lt_supply_details', 'LT Supply Details'),
        ('total_lt_ht_rooms', 'Total LT / HT Rooms'),
        ('technical_scope_others', 'Others'),
    ],
    'existing_deployment': [
        ('existing_hk_team', 'Existing HK Team'),
        ('existing_garden_team', 'Existing Garden Team'),
        ('existing_technicals', 'Existing Technicals'),
        ('existing_others', 'Existing Others'),
    ],
}

# Section 6: shift / deployment table rows.
# (description, line_type)  — descriptions are unique per survey.
SHIFT_DEPLOYMENT_ROWS = [
    ('Site Management team', 'item'),
    ('Technical(8 Hrs x 6 days)', 'item'),
    ('Tech Supervisor', 'item'),
    ('Electrician', 'item'),
    ('Plumber', 'item'),
    ('STP', 'item'),
    ('HK Supervisor', 'item'),
    ('Janitor', 'item'),
    ('Machinery', 'item'),
    ('HK Consumables', 'item'),
    ('Sub Total', 'subtotal'),
    ('TOTAL', 'total'),
]

# Section 7: locations table rows.
LOCATION_ROWS = [
    ('Common area Washroom Outside (1 gents & 1 Ladies)', 'item'),
    ('Inside Road 350m * 4 = 1400 M', 'item'),
    ('Office and admin', 'item'),
    ('Staff canteen', 'item'),
    ('Parking', 'item'),
    ('Security Cabin', 'item'),
    ('Store area/utility', 'item'),
    ('Bottling Plant', 'item'),
    ('Gardener - Landscaping area more than 5 lac sqft', 'item'),
    ('Production area', 'item'),
    ('Gardener -', 'item'),
    ('Total', 'total'),
]

# Section 8-9: equipment tables.
# (description, line_type, amortisation_months_or_none)
MAJOR_EQUIPMENT_ROWS = [
    ('Dry /wet Vacuum Cleaner', 'item', None),
    ('Single Disc Scrubbing Machine', 'item', None),
    ('cold water Jet spray machine-Partner 1210', 'item', None),
    ('Glass Cleaning Kit', 'item', None),
    ('Wringer Trolley', 'item', None),
    ("Ladder Aluminium - 10' Self Standing", 'item', None),
    ('Total (Approx.)', 'total', None),
    ('Cost of Amortisation', 'amortisation_cost', None),
    ('Equipment Maintenance & Consumables Cost', 'maintenance_cost', None),
    ('Amortized Cost for 36 months', 'amortized_monthly', 36),
    ('Total Equipment Cost per month', 'monthly_total', None),
]

MINOR_EQUIPMENT_ROWS = [
    ("Telescopic Extendable Rods 15'", 'item', None),
    ('Housekeeping Caddie', 'item', None),
    ('Safety Belts', 'item', None),
    ('Safety Helmets', 'item', None),
    ('Assorted Facility Signage', 'item', None),
    ('Garbage Bin', 'item', None),
    ('Total (Approx.)', 'total', None),
    ('Cost of Amortisation', 'amortisation_cost', None),
    ('Equipment Maintenance & Consumables Cost', 'maintenance_cost', None),
    ('Amortized Cost for 12 months', 'amortized_monthly', 12),
    ('Total Equipment Cost per month', 'monthly_total', None),
]

# Section 10: issues / improvements.
# (issue, improvement_details)
ISSUE_ROWS = [
    ('Issue with the minimum staff for Road Cleaning',
     'Need to be increase manpower and used the machinery for cleaning'),
    ('Garden area huge to maintain within staff',
     'Need to be increase manpower'),
    ('All area found bottle glass and difficult collect',
     'Need to be increase manpower and used the machinery for cleaning'),
    ('Bottle collection area very difficult to clean',
     'Need to be increase manpower and used the machinery for cleaning'),
    ('Production area - Bottle filling area- fall down when it fill',
     'Lots of liquid and glasses at down area need to used machinery for cleaning'),
    ('Production area - cleaning under Conveyor belt',
     'need to used machinery area'),
    ('Common area cleaning and utility cleaning', ''),
]

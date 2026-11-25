# Create Donation form data (historical prototype)

This note describes an earlier preview form. It is not the current Flask request contract and is not a database schema. The current root app saves authenticated donation submissions in `plentiq_dev`; inspect the current route and SQL before changing the form or schema.

```text
{
  items: [
    {
      food_name, category, description, dietary_type,
      quantity, unit, serving_size, serving_unit, item_deadline
    }
  ],
  pickup: {
    city, area, landmark, pickup_address, latitude, longitude,
    prepared_at, common_deadline, pickup_start, pickup_end
  },
  contact: {
    contact_name, country_code, phone,
    alternate_country_code, alternate_phone, email,
    use_profile, save_to_profile
  },
  confirm_accuracy
}
```

- Each item has its own category, quantity, unit, optional serving size, and optional safe deadline. The shared pickup deadline must not be later than the earliest item deadline.
- Photo handling has changed since this preview and should be checked in the current route before relying on this description.
- Latitude and longitude are optional and must be provided together. Manual address entry does not require device location or Maps.
- `datetime-local` values are interpreted as local, timezone-naive date-times by this preview. The eventual database design should choose an explicit timezone/storage convention.
- Profile and contact data are now backed by the authenticated account and MySQL. The exact current fields are defined by the current route and migrations.

For a future database design, keep the donation-level pickup/contact data shared and relate multiple food-item rows to one donation. Review this relationship before writing SQL; this note does not prescribe tables or columns.

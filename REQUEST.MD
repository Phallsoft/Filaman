# Filaman (3D Filament Spool Manager)
- User wants a basic app to manage 3d printer filament spools
- User tried Spoolman and found it to be overly complex for their needs
- User did not like fact that spoolman required each spool to be created separately, each with it's weight vs being able to enter a qty (3 x black spools, 500g each)
- User wants an experience that is as frictionless as possible.
- User does not want/need the ability to track consumption in real-time (as reported by printing software during a job)


## Tables:
- **Users**
  - Id *
  - Username
  - HashedPassword
- **Manufacturers**
  - Id *
  - Name *
  - MfgUrl
- **MaterialTypes**
  - Id *
  - Name *
- **Colors**
  - Id *
  - Name *
  - ColorCode (HTML Hex format)
- **Spools**
  - Id *
  - ManufacturerId *
  - MaterialTypeId *
  - ColorId *
  - Sku
  - Weight (grams)
- **SpoolInventory**
  - SpoolId *
  - Qty

## Supported workflows
- When creating records, user should be able to pick foreign key fields (manufacturer, material type, etc) from existing values or create new ones quicky without having to leave the current form/page.
- AI import from url or copy/pasted text
  - Create a new spool from product page URL (fetch and parse page if needed to extract relevant details).
  - This process should create any dependant records (manufacturer, material type, color) as needed if no existing match is found.
  - Allow the user to preview and edit the imported/parsed field data before ultimately saving or cancelling.

## Tech Requirements
- Docker container to be deployed with docker compose
- Python based
- No preference on database
- Clean, modern, mobile friendly UI with light/dark theme
- Form based authentication

## Misc
- Prompt user to create user/password on initial load
- Admin options
  - Change password
  - Database backup/restore
  - Reset database (call out as dangerous action, make user type "Yes, Really Reset The Database" to confirm)
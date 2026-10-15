// Demo roles for rolefence_demo: views, DB roles and users for two app roles plus one
// deliberately over-powered user for the "weak" setup.
//
// Run as a MongoDB admin, with the new passwords in the shell (letters and digits only):
//
//   read -s "DEMO_SALES_PW?new demo_sales password: " && echo && \
//   read -s "DEMO_FINANCE_PW?new demo_finance password: " && echo && \
//   read -s "DEMO_RW_PW?new demo_rw password: " && echo && \
//   export DEMO_SALES_PW DEMO_FINANCE_PW DEMO_RW_PW && \
//   mongosh --quiet --username <admin> --authenticationDatabase admin \
//     "mongodb://127.0.0.1:27017/admin" demo/setup_users.js
//
// Roles:
//   sales   - customers, enquiries, messages, and orders without supplier_cost (view orders_sales)
//   finance - everything in rolefence_demo
//   demo_rw - readWrite on the whole database (the weak setup; the scanner must flag it)

const dbName = "rolefence_demo";
const lab = db.getSiblingDB(dbName);

for (const v of ["SALES", "FINANCE", "RW"]) {
  if (!process.env[`DEMO_${v}_PW`]) {
    throw new Error(`DEMO_${v}_PW is not set in the environment`);
  }
}

// Views for the sales role. supplier_cost and supplier_id are left out of orders.
lab.orders_sales.drop();
lab.createView("orders_sales", "orders", [
  { $project: { supplier_cost: 0, supplier_id: 0 } },
]);

// Roles. Views are granted one by one; the base collections are not.
for (const r of ["demo_sales_reader", "demo_finance_reader"]) {
  try { lab.dropRole(r); } catch (e) { /* not there yet */ }
}
lab.createRole({
  role: "demo_sales_reader",
  privileges: [
    { resource: { db: dbName, collection: "customers" }, actions: ["find"] },
    { resource: { db: dbName, collection: "enquiries" }, actions: ["find"] },
    { resource: { db: dbName, collection: "messages" }, actions: ["find"] },
    { resource: { db: dbName, collection: "orders_sales" }, actions: ["find"] },
  ],
  roles: [],
});
lab.createRole({
  role: "demo_finance_reader",
  privileges: [],
  roles: [{ role: "read", db: dbName }],
});

// Users live in rolefence_demo (authSource=rolefence_demo).
for (const u of ["demo_sales", "demo_finance", "demo_rw"]) {
  try { lab.dropUser(u); } catch (e) { /* not there yet */ }
}
lab.createUser({ user: "demo_sales", pwd: process.env.DEMO_SALES_PW,
                 roles: [{ role: "demo_sales_reader", db: dbName }] });
lab.createUser({ user: "demo_finance", pwd: process.env.DEMO_FINANCE_PW,
                 roles: [{ role: "demo_finance_reader", db: dbName }] });
lab.createUser({ user: "demo_rw", pwd: process.env.DEMO_RW_PW,
                 roles: [{ role: "readWrite", db: dbName }] });

print("created view orders_sales, roles demo_sales_reader / demo_finance_reader,");
print("users demo_sales / demo_finance / demo_rw in " + dbName);
print("connection strings (passwords from the same shell variables):");
print("  DEMO_SALES_URI=\"mongodb://demo_sales:${DEMO_SALES_PW}@127.0.0.1:27017/" + dbName + "?authSource=" + dbName + "\"");
print("  DEMO_FINANCE_URI=\"mongodb://demo_finance:${DEMO_FINANCE_PW}@127.0.0.1:27017/" + dbName + "?authSource=" + dbName + "\"");
print("  DEMO_RW_URI=\"mongodb://demo_rw:${DEMO_RW_PW}@127.0.0.1:27017/" + dbName + "?authSource=" + dbName + "\"");

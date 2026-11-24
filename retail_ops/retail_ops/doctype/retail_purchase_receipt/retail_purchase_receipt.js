frappe.ui.form.on("Retail Purchase Receipt", {
	onload(frm) {
		if (frm.is_new() && !frm.doc.receipt_date) {
			frm.set_value("receipt_date", frappe.datetime.get_today());
		}
	},
	refresh(frm) {
		frm.set_query("warehouse", () => ({
			filters: { branch: frm.doc.branch },
		}));
	},
	branch(frm) {
		if (frm.doc.warehouse) {
			frm.set_value("warehouse", null);
		}
	},
});

frappe.ui.form.on("Retail Purchase Receipt Item", {
	product(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.product) return;
		frappe.db.get_value("Retail Product", row.product, ["rate", "mrp"], (value) => {
			if (!value) return;
			if (flt(value.mrp)) {
				frappe.model.set_value(cdt, cdn, "mrp", value.mrp);
			}
			if (!flt(row.rate) && flt(value.rate)) {
				frappe.model.set_value(cdt, cdn, "rate", value.rate);
			}
		});
	},
	qty(frm, cdt, cdn) {
		set_amount(cdt, cdn);
	},
	rate(frm, cdt, cdn) {
		set_amount(cdt, cdn);
	},
});

function set_amount(cdt, cdn) {
	const row = locals[cdt][cdn];
	frappe.model.set_value(cdt, cdn, "amount", flt(row.qty) * flt(row.rate));
}

frappe.ui.form.on("Retail Stock Transfer", {
	onload(frm) {
		if (frm.is_new() && !frm.doc.transfer_date) {
			frm.set_value("transfer_date", frappe.datetime.get_today());
		}
	},
	refresh(frm) {
		frm.set_query("from_warehouse", () => ({
			filters: { branch: frm.doc.branch },
		}));
		frm.set_query("to_warehouse", () => ({
			filters: { branch: frm.doc.to_branch },
		}));
	},
	branch(frm) {
		frm.set_value("from_warehouse", null);
	},
	to_branch(frm) {
		frm.set_value("to_warehouse", null);
	},
	// a single-warehouse shop only ever moves sideways, so start with the
	// branch it is already on filled in for both ends
	from_warehouse(frm) {
		if (!frm.doc.to_branch && frm.doc.branch) {
			frm.set_value("to_branch", frm.doc.branch);
		}
	},
});

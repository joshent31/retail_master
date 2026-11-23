frappe.ui.form.on("Retail Offer", {
	refresh(frm) {
		if (frm.is_new()) {
			frm.set_value("valid_from", frappe.datetime.get_today());
		}
	},
	apply_on(frm) {
		if (frm.doc.apply_on === "Category") {
			frm.set_value("product", null);
		} else {
			frm.set_value("category", null);
		}
	},
	discount_type(frm) {
		if (frm.doc.discount_type === "Percent") {
			frm.set_df_property("value", "label", "Percent Off");
		} else {
			frm.set_df_property("value", "label", "Amount Off Per Unit");
		}
	},
});

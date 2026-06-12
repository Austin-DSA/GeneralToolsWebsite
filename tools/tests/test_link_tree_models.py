from django.core.exceptions import ValidationError
from django.test import TestCase

from tools.models import LinkTree, LinkTreeItem, QRCode


# --- model invariants ------------------------------------------------------


class QRCodeModelTests(TestCase):
    def setUp(self):
        self.tree = LinkTree.objects.create(slug="links", title="Links")

    def test_exactly_one_target_required(self):
        with self.assertRaises(ValidationError):
            QRCode(code="none", label="No target").full_clean()
        with self.assertRaises(ValidationError):
            QRCode(code="two", label="Two", tree=self.tree, rawUrl="https://x.org").full_clean()
        # One target is fine.
        QRCode(code="one", label="One", tree=self.tree).full_clean()

    def test_target_url_resolution_order(self):
        qr = QRCode.objects.create(code="t", label="T", tree=self.tree)
        self.assertEqual(qr.targetUrl(), self.tree.getPublicUrl())

    def test_resolve_target_returns_destination_and_attribution(self):
        item = LinkTreeItem.objects.create(
            tree=self.tree, order=0, kind=LinkTreeItem.Kind.MANUAL,
            label="X", url="https://example.org/x",
        )
        # Tree target → attribute to the tree, no item.
        dest, tree, it = QRCode(code="qt", label="qt", tree=self.tree).resolveTarget()
        self.assertEqual((dest, tree, it), (self.tree.getPublicUrl(), self.tree, None))
        # Item target → destination is the item's url, attributed to item + its tree.
        dest, tree, it = QRCode(code="qi", label="qi", item=item).resolveTarget()
        self.assertEqual((dest, tree, it), ("https://example.org/x", self.tree, item))
        # Raw url → no tree/item attribution.
        dest, tree, it = QRCode(code="qr", label="qr", rawUrl="https://raw.example").resolveTarget()
        self.assertEqual((dest, tree, it), ("https://raw.example", None, None))


class LinkTreeItemTests(TestCase):
    def test_header_has_no_tracked_url(self):
        tree = LinkTree.objects.create(slug="h", title="H")
        header = LinkTreeItem.objects.create(
            tree=tree, order=0, kind=LinkTreeItem.Kind.SECTION_HEADER, label="Section",
        )
        self.assertIsNone(header.trackedUrl())
        self.assertTrue(header.shouldDisplay())  # headers always show
        self.assertFalse(header.isResolved())

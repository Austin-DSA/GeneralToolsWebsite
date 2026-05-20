from django import forms
from enum import StrEnum




class VoteResolutionForm(forms.Form):
    class Keys:
        VOTE = "vote"

    class VoteChoices(StrEnum):
        YES = "YES"
        NO = "NO"
        ABSTAIN = "ABSTAIN"

    vote = forms.ChoiceField(
        choices=[c.value for c in VoteChoices], label="Should Austin DSA Adopt this Resolution?"
    )

    def getVote(self) -> VoteChoices | None:
        if not self.is_valid():
            return None
        return self.cleaned_data[VoteResolutionForm.Keys.VOTE]

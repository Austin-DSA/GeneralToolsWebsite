from huey.contrib.djhuey import db_task
from .models import Resolution, ResolutionVote
from ..EmailApi import EmailApi
import time
import datetime
from ..utils import DATE_TIME_FORMAT
from ..ActionNetworkAPI import ActionNetoworkAPI
from ..SecretManager import SecretManager
import logging

def deduplicateVotes(votes, skipUnverifiedVotes: bool) -> dict[str,ResolutionVote]:
    deduplicatedVotes = {}
    for vote in votes:
        if not vote.checkedForVerification and skipUnverifiedVotes:
            continue
        if vote.email in deduplicatedVotes:
            if vote.casted > deduplicatedVotes[vote.email].casted:
                deduplicatedVotes[vote.email] = vote
        else:
            deduplicatedVotes[vote.email] = vote
    return deduplicatedVotes

@db_task
def validateVotes(resId):
    resolution : Resolution = Resolution.objects.get(resId)
    votes = ResolutionVote.objects.filter(resolution=resolution)
    deduplicateVotes = deduplicateVotes(votes)
    anAPI = ActionNetoworkAPI.ActionNetworkAPI(SecretManager.getANAPIKey()) 
    abstainCount = 0
    yesCount = 0
    noCount = 0
    for email,vote in deduplicateVotes.items():
        try:
            vote.checkedForVerification = True
            vote.whenVerified = datetime.datetime.now(datetime.UTC)

            (apiStatus, personInfo) = anAPI.getPersonForVoteValidation(email=email)
            # Check API failures
            if apiStatus == ActionNetoworkAPI.GetPersonAPIReturnStatus.INVALID_API_RESPONSE:
                vote.verificationError = ResolutionVote.VerificationError.INVALID_API_RESPONSE
            elif apiStatus == ActionNetoworkAPI.GetPersonAPIReturnStatus.NOT_FOUND:
                vote.verificationError = ResolutionVote.VerificationError.NOT_FOUND
            elif apiStatus == ActionNetoworkAPI.GetPersonAPIReturnStatus.MULTIPLE_RECORDS_RETURNED:
                vote.verificationError = ResolutionVote.VerificationError.MULTIPLE_RECORDS_RETURNED
            elif apiStatus == ActionNetoworkAPI.GetPersonAPIReturnStatus.MISSING_REQUIRED_CUSTOM_FIELDS:
                vote.verificationError = ResolutionVote.VerificationError.MISSING_REQUIRED_CUSTOM_FIELDS
            elif personInfo is None:
                logging.error("Unexpected null person for %s", email)
                vote.verificationError = ResolutionVote.VerificationError.UNKOWN
            # Check MIGS status, if migs we are done
            elif personInfo.memberStatus:
                vote.verificationError = ResolutionVote.VerificationError.NO_ERROR
                if vote.vote == ResolutionVote.VoteChoices.ABSTAIN:
                    abstainCount += 1
                elif vote.vote == ResolutionVote.VoteChoices.YES:
                    yesCount += 1
                elif vote.vote == ResolutionVote.VoteChoices.NO:
                    noCount += 1
                continue
            # If not migs check for common cases for errors
            elif personInfo.chapter.lower() != "austin":
                vote.verificationError = ResolutionVote.VerificationError.INCORRECT_CHAPTER
            elif personInfo.expireDate < datetime.date.today():
                vote.verificationError = ResolutionVote.VerificationError.EXPIRED
            else:
                vote.verificationError = ResolutionVote.VerificationError.UNKOWN
            vote.save()
        except Exception as err:
            logging.exception("Unexpected exception for %s, %s", email, str(err))
            vote.checkedForVerification = True
            vote.whenVerified = datetime.datetime.now(datetime.UTC)
            vote.verificationError = ResolutionVote.VerificationError.UNKOWN
            vote.save()
            continue
    resolution.whenLastValidated = datetime.datetime.now(datetime.UTC)
    resolution.lastValidatedCountAbstain = abstainCount
    resolution.lastValidatedCountNo = noCount
    resolution.lastValidatedCountYes = yesCount
    resolution.save()




def getVerificationErrorHelp(verificationError: ResolutionVote.VerificationError) -> str:
    if verificationError == ResolutionVote.VerificationError.NO_ERROR:
        return ""
    if verificationError == ResolutionVote.VerificationError.NOT_FOUND:
        return """
        We could not find this email in our lists.
        This is most likely because you used an email not associated with your membership or you are not a member.
        If you are confident you are a member, please contact membership@austindsa.org or membership@dsausa.org.
        """
    if verificationError == ResolutionVote.VerificationError.INVALID_API_RESPONSE:
        return """
        We recieved an invalid response from Action Network when validating.
        Please contact membership@austindsa.org or the secretary on slack with evidence of membership from https://proof.dsausa.org/card
        """
    if verificationError == ResolutionVote.VerificationError.MISSING_REQUIRED_CUSTOM_FIELDS:
        return """
        Your record in our list is missing required fields to determine your membership.
        This is most likely becuase this email is on mailing list but is not associated with a membership with DSA.
        This either means you used the incorrect email or you not a member.
        If you are not member, please consider joining at https://act.dsausa.org/donate/membership/.
        If you are confident you are a member, please contact membership@austindsa.org or membership@dsausa.org.
        """
    if verificationError == ResolutionVote.VerificationError.MULTIPLE_RECORDS_RETURNED:
        return """
        We found multiple records in our list for this email.
        This is very strange.
        Please contact membership@austindsa.org.
        """
    if verificationError == ResolutionVote.VerificationError.EXPIRED:
        return """
        We have in our list that your membership has expired.
        You can recommit at https://act.dsausa.org/donate/membership/
        If you confident that you are current on dues, please contact membership@austindsa.org.
        """
    if verificationError == ResolutionVote.VerificationError.INCORRECT_CHAPTER:
        return """
        You are not listed as being in the correct chapter in our lists.
        This most likely is because you moved and your mailing address is still listed for your old chapter.
        You can request a chapter change and update your mailing address here: https://act.dsausa.org/survey/request_to_change_your_chapter/
        """
    return f"Unexpected Error {verificationError}"

# TODO: For genericization will need to update this to not hardcode Austin DSA
def getEmailBody(resolution: Resolution, vote: ResolutionVote) -> str:
    verificationErrorString = getVerificationErrorHelp(vote.verificationError)
    return f"""
    Title: {resolution.name}
    Status: {resolution.status()}
    Aye-Nay-Abstain: {resolution.lastValidatedCountYes}-{resolution.lastValidatedCountNo}-{resolution.lastValidatedCountAbstain}

    We recorded your vote for {vote.name} as {vote.get_vote_display()}.
    Your vote was considered {"VALID" if vote.verificationError == ResolutionVote.VerificationError.NO_ERROR else "INVALID"}.
    This vote was cast on {vote.casted.strftime(DATE_TIME_FORMAT)} and checked for verification on {vote.whenVerified.strftime(DATE_TIME_FORMAT)}.
    Note the above timestamps may be UTC not your local timezone.

    {verificationErrorString}

    For any questions please contact leadership@austindsa.org or the secretary on slack.
    Solidarity,
    Austin DSA Bot
    """

def getEmailBodyForFailedVote(resolution: Resolution, vote: ResolutionVote) -> str:
    verificationErrorString = getVerificationErrorHelp(vote.verificationError)
    return f"""
    Title: {resolution.name}

    We recorded your vote for {vote.name} as {vote.get_vote_display()}.
    Your vote was considered {"VALID" if vote.verificationError == ResolutionVote.VerificationError.NO_ERROR else "INVALID"}.
    This vote was cast on {vote.casted.strftime(DATE_TIME_FORMAT)} and checked for verification on {vote.whenVerified.strftime(DATE_TIME_FORMAT)}.
    Note the above timestamps may be UTC not your local timezone.

    {verificationErrorString}

    For any questions please contact leadership@austindsa.org or the secretary on slack.
    Solidarity,
    Austin DSA Bot
    """

@db_task
def emailVoteResolutionResult(resId):
    resolution : Resolution = Resolution.objects.get(resId)
    votes = ResolutionVote.objects.filter(resolution=resolution)
    deduplicateVotes = deduplicateVotes(votes)
    for email,vote in deduplicateVotes.items():
        if not vote.checkedForVerification:
            continue
        EmailApi.sendEmailFromWebsiteAccount(
            toAddress=email,
            subject=f"Result for Resolution: {resolution.name}",
            messageText=getEmailBody(resolution=resolution, vote=vote)
            )
        # Sleep a little bit so we don't seem spammy to email providers, probably not worth it but why not
        time.sleep(1)

@db_task
def emailFailedVotesForResolution(resId):
    resolution : Resolution = Resolution.objects.get(resId)
    votes = ResolutionVote.objects.filter(resolution=resolution)
    deduplicateVotes = deduplicateVotes(votes)
    for email,vote in deduplicateVotes.items():
        # Skip votes we haven't checked or aren't failed
        if not vote.checkedForVerification or vote.verificationError == ResolutionVote.VerificationError.NO_ERROR:
            continue
        EmailApi.sendEmailFromWebsiteAccount(
            toAddress=email,
            subject=f"Error Validating Vote: {resolution.name}",
            messageText=getEmailBodyForFailedVote(resolution=resolution, vote=vote)
            )
        # Sleep a little bit so we don't seem spammy to email providers, probably not worth it but why not
        time.sleep(1)
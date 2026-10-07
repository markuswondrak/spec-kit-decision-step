# Test Issue: Session token appears in application logs

After a user signs in, an application debug log line may include the complete
session token. Anyone with routine log access could reuse that token while it
remains valid. The issue needs triage and a decision about whether to involve
the security team.

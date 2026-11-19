// Run once by the trusted controller administrator, never as a PR build step.
// The protected input is deleted after Jenkins encrypts the dedicated credential.
import jenkins.model.Jenkins
import jenkins.model.JenkinsLocationConfiguration
import groovy.json.JsonSlurper
import com.cloudbees.plugins.credentials.CredentialsProvider
import com.cloudbees.plugins.credentials.CredentialsScope
import com.cloudbees.plugins.credentials.domains.Domain
import com.cloudbees.plugins.credentials.impl.UsernamePasswordCredentialsImpl
import com.cloudbees.hudson.plugins.folder.computed.PeriodicFolderTrigger
import com.cloudbees.hudson.plugins.folder.computed.DefaultOrphanedItemStrategy
import jenkins.branch.BranchSource
import org.jenkinsci.plugins.github_branch_source.GitHubSCMSource
import org.jenkinsci.plugins.github_branch_source.OriginPullRequestDiscoveryTrait
import org.jenkinsci.plugins.workflow.multibranch.WorkflowMultiBranchProject
import org.jenkinsci.plugins.workflow.multibranch.WorkflowBranchProjectFactory

def j = Jenkins.get()
def tokenFile = new File(j.rootDir, 'koipond-github-token.json')
if (!tokenFile.isFile()) {
  throw new IllegalStateException('Dedicated protected GitHub credential has not been provided')
}
def input = new JsonSlurper().parse(tokenFile)
if (!(input.token instanceof String) || input.token.length() < 20 || input.login != 't1ggydev') {
  throw new IllegalStateException('Invalid dedicated credential input')
}
def project = j.getItem('Koipond-PR')
if (project != null && !(project instanceof WorkflowMultiBranchProject)) {
  throw new IllegalStateException('Existing Koipond-PR item has an unexpected type')
}
if (project == null) project = j.createProject(WorkflowMultiBranchProject, 'Koipond-PR')
def store = CredentialsProvider.lookupStores(project).find { it.context == project }
if (store == null) throw new IllegalStateException('Folder-scoped credential store is unavailable')
def credential = new UsernamePasswordCredentialsImpl(CredentialsScope.GLOBAL,
  'koipond-github-status', 'Koipond only: source/PR read and commit status write',
  input.login, input.token)
def previous = store.getCredentials(Domain.global()).find { it.id == credential.id }
def saved = previous == null ? store.addCredentials(Domain.global(), credential) :
  store.updateCredentials(Domain.global(), previous, credential)
if (!saved) throw new IllegalStateException('Could not persist encrypted folder credential')
if (!tokenFile.delete()) throw new IllegalStateException('Protected temporary input cleanup failed')
input = null

def source = new GitHubSCMSource('t1ggydev', 'koipond')
source.setId('koipond-origin-pr-heads')
source.setCredentialsId('koipond-github-status')
// No fork discovery: only pull requests originating inside this owned repository.
source.setTraits([new OriginPullRequestDiscoveryTrait(2)])
project.sourcesList.replaceBy([new BranchSource(source)])
def factory = new WorkflowBranchProjectFactory()
factory.setScriptPath('deploy/Jenkinsfile')
project.setProjectFactory(factory)
project.setDescription('Origin PR head checks only; no production or private-stage deployment from PR builds.')
project.setOrphanedItemStrategy(new DefaultOrphanedItemStrategy(true, '14', '30'))
project.addTrigger(new PeriodicFolderTrigger('2m'))
project.save()
def location = JenkinsLocationConfiguration.get()
if (!location.getUrl()) {
  location.setUrl('http://10.1.0.102:8080/')
  location.save()
}
project.scheduleBuild2(0)
println('Koipond PR head indexing configured; native Jenkins statuses follow real build results.')

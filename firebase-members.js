import { initializeApp } from 'https://www.gstatic.com/firebasejs/13.4.0/firebase-app.js';
import { getAuth, createUserWithEmailAndPassword, signInWithEmailAndPassword, onAuthStateChanged, signOut, sendPasswordResetEmail } from 'https://www.gstatic.com/firebasejs/13.4.0/firebase-auth.js';
import { getFirestore, doc, getDoc, setDoc, updateDoc, collection, getDocs, query, where, serverTimestamp, runTransaction } from 'https://www.gstatic.com/firebasejs/13.4.0/firebase-firestore.js';

const firebaseConfig = {
  apiKey: 'AIzaSyCFUjKRCQUlg1wyMEHqc0ttkRMjqYkV_qE',
  authDomain: 'market-sanket.firebaseapp.com',
  projectId: 'market-sanket',
  appId: '1:371783165561:web:8008cfd9d891d17e9096a8'
};
const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const db = getFirestore(app);
const $ = (id) => document.getElementById(id);
const gate = $('memberGate');
const form = $('memberForm');
const nameField = $('nameField');
const nameInput = $('memberName');
const emailInput = $('memberEmail');
const passwordInput = $('memberPassword');
const authLinks = $('authLinks');
const statusPanel = $('accountStatus');
const appShell = $('appShell');
const adminPanel = $('adminPanel');
let mode = 'signin';
let activeAdmin = false;
let accessCheckSequence = 0;

function say(message, kind) {
  const box = $('authNotice');
  box.textContent = message || '';
  box.className = 'member-message' + (kind ? ' ' + kind : '');
}
function status(message, kind) {
  $('statusMessage').textContent = message;
  $('statusMessage').className = 'member-message' + (kind ? ' ' + kind : '');
}
function setMode(next) {
  mode = next;
  const signup = mode === 'signup';
  nameField.hidden = !signup;
  nameInput.required = signup;
  $('gateTitle').textContent = signup ? 'Request membership' : 'Sign in to Share Sanket';
  $('gateIntro').textContent = signup ? 'Create an account. Your request stays pending until the admin approves it.' : 'Sign in with the email and password you registered with.';
  $('authSubmit').textContent = signup ? 'Send approval request' : 'Sign in';
  $('authModeToggle').textContent = signup ? 'Already registered? Sign in' : 'Request membership';
  say('');
}
function explainError(error) {
  const code = error && error.code;
  if (code === 'auth/invalid-credential' || code === 'auth/wrong-password' || code === 'auth/user-not-found') return 'Email or password is incorrect.';
  if (code === 'auth/email-already-in-use') return 'This email already has an account. Choose Sign in.';
  if (code === 'auth/weak-password') return 'Use a password with at least 6 characters.';
  if (code === 'auth/invalid-email') return 'Enter a valid email address.';
  if (code === 'auth/unauthorized-domain') return 'This website domain is not enabled in Firebase Authentication yet. Add the app domain under Authentication → Settings → Authorized domains.';
  if (code === 'permission-denied' || (error && /permission/i.test(error.message || ''))) return 'Firebase database rules are not installed yet. Ask the admin to finish the setup before registering.';
  if (error && error.message === 'MEMBER_LIMIT') return 'All 30 member places are filled. No additional request can be approved.';
  return (error && error.message) || 'Something went wrong. Please try again.';
}
function showSignedOut() {
  accessCheckSequence += 1;
  activeAdmin = false;
  appShell.hidden = true;
  gate.hidden = false;
  statusPanel.hidden = true;
  form.hidden = false;
  authLinks.hidden = false;
  $('memberControls').hidden = true;
  adminPanel.hidden = true;
  setMode('signin');
}
function grantAccess(user, profile, isAdmin) {
  activeAdmin = isAdmin;
  gate.hidden = true;
  appShell.hidden = false;
  $('memberControls').hidden = false;
  $('sessionLabel').textContent = (isAdmin ? 'Admin · ' : '') + ((profile && profile.displayName) || user.displayName || user.email || 'Member');
  $('adminOpen').hidden = !isAdmin;
  if (typeof window.ssMemberStart === 'function') window.ssMemberStart();
}
async function checkAccess(user) {
  if (!user) return;
  const sequence = ++accessCheckSequence;
  gate.hidden = false;
  form.hidden = true;
  authLinks.hidden = true;
  statusPanel.hidden = false;
  $('memberControls').hidden = true;
  status('Checking your membership…');
  try {
    const adminSnap = await getDoc(doc(db, 'admins', user.uid));
    if (sequence !== accessCheckSequence) return;
    if (adminSnap.exists() && adminSnap.data().role === 'admin') {
      grantAccess(user, null, true);
      return;
    }
    const memberSnap = await getDoc(doc(db, 'members', user.uid));
    if (sequence !== accessCheckSequence) return;
    if (!memberSnap.exists()) {
      status('No membership request is saved for this account. Sign out and request membership, or ask the admin for help.', 'error');
      $('memberUid').textContent = user.uid;
      return;
    }
    const profile = memberSnap.data();
    $('memberUid').textContent = user.uid;
    if (profile.status === 'approved') {
      grantAccess(user, profile, false);
    } else if (profile.status === 'rejected') {
      status('Your membership request was not approved. Contact the Share Sanket admin if you think this is a mistake.', 'error');
    } else {
      status('Your request is pending. The admin will review it before you can access Share Sanket.');
    }
  } catch (error) {
    if (sequence === accessCheckSequence) status(explainError(error), 'error');
  }
}

$('authModeToggle').addEventListener('click', () => setMode(mode === 'signin' ? 'signup' : 'signin'));
$('resetPassword').addEventListener('click', async () => {
  const email = emailInput.value.trim();
  if (!email) { say('Enter your email address first, then choose Forgot password.', 'error'); return; }
  try { await sendPasswordResetEmail(auth, email); say('Password reset email sent. Check your inbox.'); }
  catch (error) { say(explainError(error), 'error'); }
});
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = $('authSubmit');
  button.disabled = true;
  say(mode === 'signup' ? 'Submitting your request…' : 'Signing in…');
  try {
    const email = emailInput.value.trim();
    if (mode === 'signup') {
      const displayName = nameInput.value.trim();
      if (displayName.length < 2) throw new Error('Please enter your name.');
      const credential = await createUserWithEmailAndPassword(auth, email, passwordInput.value);
      await setDoc(doc(db, 'members', credential.user.uid), {
        displayName,
        email: credential.user.email,
        status: 'pending',
        createdAt: serverTimestamp(),
        updatedAt: serverTimestamp()
      });
      $('memberUid').textContent = credential.user.uid;
      status('Request submitted. Your account will get access after the admin approves it.');
      form.hidden = true;
      authLinks.hidden = true;
      statusPanel.hidden = false;
      await checkAccess(credential.user);
    } else {
      await signInWithEmailAndPassword(auth, email, passwordInput.value);
    }
  } catch (error) {
    say(explainError(error), 'error');
  } finally {
    button.disabled = false;
  }
});
async function doSignOut() {
  if (typeof window.ssMemberStop === 'function') window.ssMemberStop();
  await signOut(auth);
  showSignedOut();
}
$('waitSignOut').addEventListener('click', () => doSignOut().catch((e) => status(explainError(e), 'error')));
$('signOutTop').addEventListener('click', () => doSignOut().catch(() => {}));
$('copyUid').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('memberUid').textContent); $('copyUid').textContent = 'Copied'; setTimeout(() => $('copyUid').textContent = 'Copy account ID', 1500); }
  catch (_) { status('Select and copy the account ID shown above.', 'error'); }
});

function makeMemberRow(memberDoc, state) {
  const data = memberDoc.data();
  const row = document.createElement('div');
  row.className = 'member-row';
  const details = document.createElement('div');
  details.className = 'member-row-details';
  const name = document.createElement('strong');
  name.textContent = data.displayName || 'Name not provided';
  const email = document.createElement('span');
  email.textContent = data.email || 'Email unavailable';
  details.append(name, email);
  row.appendChild(details);
  if (state === 'pending') {
    const actions = document.createElement('div');
    actions.className = 'member-row-actions';
    const approve = document.createElement('button');
    approve.type = 'button'; approve.className = 'btn'; approve.textContent = 'Approve';
    approve.addEventListener('click', () => changeMemberStatus(memberDoc.id, 'approved', approve));
    const reject = document.createElement('button');
    reject.type = 'button'; reject.className = 'secondary-btn'; reject.textContent = 'Reject';
    reject.addEventListener('click', () => changeMemberStatus(memberDoc.id, 'rejected', reject));
    actions.append(approve, reject);
    row.appendChild(actions);
  } else {
    const badge = document.createElement('span'); badge.className = 'member-status-badge'; badge.textContent = 'Approved'; row.appendChild(badge);
  }
  return row;
}
async function loadAdminMembers() {
  $('pendingUsers').textContent = '';
  $('approvedUsers').textContent = '';
  $('adminNotice').textContent = 'Loading member requests…';
  try {
    const [pendingSnap, approvedSnap, quotaSnap] = await Promise.all([
      getDocs(query(collection(db, 'members'), where('status', '==', 'pending'))),
      getDocs(query(collection(db, 'members'), where('status', '==', 'approved'))),
      getDoc(doc(db, 'settings', 'membership'))
    ]);
    const used = quotaSnap.exists() ? Number(quotaSnap.data().activeMembers || 0) : approvedSnap.size;
    $('memberCapText').textContent = 'Approved member places: ' + used + ' / 30 (admin account is separate).';
    if (!pendingSnap.size) $('pendingUsers').textContent = 'No pending requests.';
    else pendingSnap.forEach((memberDoc) => $('pendingUsers').appendChild(makeMemberRow(memberDoc, 'pending')));
    if (!approvedSnap.size) $('approvedUsers').textContent = 'No approved members yet.';
    else approvedSnap.forEach((memberDoc) => $('approvedUsers').appendChild(makeMemberRow(memberDoc, 'approved')));
    $('adminNotice').textContent = '';
  } catch (error) {
    $('adminNotice').textContent = explainError(error);
    $('adminNotice').className = 'member-message error';
  }
}
async function changeMemberStatus(uid, nextStatus, button) {
  button.disabled = true;
  $('adminNotice').textContent = nextStatus === 'approved' ? 'Approving member…' : 'Rejecting request…';
  $('adminNotice').className = 'member-message';
  try {
    const memberRef = doc(db, 'members', uid);
    if (nextStatus === 'approved') {
      const quotaRef = doc(db, 'settings', 'membership');
      await runTransaction(db, async (transaction) => {
        const [memberSnap, quotaSnap] = await Promise.all([transaction.get(memberRef), transaction.get(quotaRef)]);
        if (!memberSnap.exists()) throw new Error('This membership request no longer exists.');
        if (memberSnap.data().status === 'approved') return;
        const used = quotaSnap.exists() ? Number(quotaSnap.data().activeMembers || 0) : 0;
        if (used >= 30) throw new Error('MEMBER_LIMIT');
        transaction.set(quotaRef, { activeMembers: used + 1, limit: 30, updatedAt: serverTimestamp() }, { merge: true });
        transaction.update(memberRef, { status: 'approved', approvedAt: serverTimestamp(), updatedAt: serverTimestamp() });
      });
    } else {
      await updateDoc(memberRef, { status: 'rejected', rejectedAt: serverTimestamp(), updatedAt: serverTimestamp() });
    }
    $('adminNotice').className = 'member-message success';
    $('adminNotice').textContent = nextStatus === 'approved' ? 'Member approved.' : 'Request rejected.';
    await loadAdminMembers();
  } catch (error) {
    $('adminNotice').className = 'member-message error';
    $('adminNotice').textContent = explainError(error);
    button.disabled = false;
  }
}
$('adminOpen').addEventListener('click', () => { adminPanel.hidden = false; loadAdminMembers(); });
$('adminClose').addEventListener('click', () => { adminPanel.hidden = true; });
adminPanel.addEventListener('click', (event) => { if (event.target === adminPanel) adminPanel.hidden = true; });
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') adminPanel.hidden = true; });
onAuthStateChanged(auth, (user) => {
  if (!user) { showSignedOut(); return; }
  checkAccess(user);
});
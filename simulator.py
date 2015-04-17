#!/usr/bin/python
"""Simulates cashflow and wealth accumulation."""

import copy
import croniter
import datetime
import gflags
import logging
import random
import sys
import simulator_pb2

from collections import defaultdict
from google.protobuf.text_format import Merge


gflags.DEFINE_bool('variance', False,
    'Add real-world variance to stock performance and credit card bills',
    short_name='v')

gflags.DEFINE_string('config', 'simulator.pb',
    'Path to the configuration file text protobuf.',
    short_name='c')

FLAGS = gflags.FLAGS


class Error(Exception):
  """Base class for errors."""


class InvalidSweepError(Error):
  """When a sweep is defined to an incorrect account."""


class CronError(Error):
  """Specified cron is not advancing to the next date."""


class Account(object):
  """An asset or debt such as checking, brokerage, mortgage.

  These accounts can appreciate or depreciate, have optional maximum and minimum
  values, and define where they can sweep into or out of.

  Optionally a timeframe may be defined for when a sweep may happen. For
  example, you are unlikely to take excess cash you have and buy securities as
  soon as your cash exceeds the maximum defined - you may only do this
  quarterly.

  Attributes:
    name: string; Name of the account.
    value: float; US dollar equivalent value.
    max: float; If a sweep rule is defined for this account it will wisk away
         money above max. This is a property that pulls from sweep_out.
    min: float; If a sweep rule is defined for this account it will pull in
         money to achieve min. This is a property that pulls from sweep_in.
    liquidity: float; Days required to turn into cash.
    sweep_out: SweepRule object; What to do when we have too much.
    sweep_in: Sweep object; What to do when we have to little.
    timespec: croniter object; When to compound.
    rate: float; APR.
    stddev: float; If you run main with variance enabled, how far can the
            random preciation be.
    start_date: str; An iso format date string for the start date of a loan.
                This is used to calculate a fixed rate loan schedule.
    loan_months: int; The number of months the loan is for.

  Methods:
    Preciate: Appreciate or depreciate.
  """

  def __init__(self, account_name, starting_value):
    """Initiator.

    Args:
      account_name: str; The name of the account such as "savings".
      starting_value: float; The initial balance in dollars of the account.
    """
    self.name = account_name.lower()
    self.value = starting_value

    # These are optionally loaded from the config file.
    self.liquidity = 0.0
    self.sweep_out = None
    self.sweep_in = None
    self.timespec = None
    self.start_date = None
    self.rate = 0.0
    self.stddev = 0.0
    # Populated by BuildAmortization, key is datetime.date, value is tuple of
    # interest, principle.
    self._amortization = {}
    self._payment = None

  # TODO: create a __str__ method which creates a text protobuf.

  def Preciate(self):
    """Appreciate or depreciate.

    This function has two modes, it can update an account based on an
    amortization schedule, or it can simply use the rate.

    Based on the time between the current date and the next preciation,
    determine the number of days, and subsequent fraction of the yearly rate.
    Appreciate or depreciate accordingly.
    """

    current_date = self.timespec.get_current(
        datetime.datetime).date()

    # We need to know the next date here for calculating rate fraction, but also
    # at the GeneralLedger level for updating preciations.
    next_date = copy.copy(self.timespec).get_next(
        datetime.datetime).date()

    # If there is an amortization schedule (which is created when there is a
    # start date for the account) use that to update the value.
    _, principle = self._amortization.get(current_date, (None, None))
    if principle:
      self.value += principle
      logging.debug('Found an amortization entry for %s on %s. Paid off %s '
                    'principle, %s remaining.', self.name, current_date,
                    principle, -self.value)
      return
    elif self.name == 'mortgage':
      print "### WTF, no amortization entry for", current_date
      return
    rate_fraction = self._RateFraction(next_date, current_date)
    self.value = self.value * (1 + rate_fraction)

  def _RateFraction(self, next_date, current_date):
    """Returns interest rate for the delta between two datetime.dates.

    The self.rate is an APR. Given an arbitrary pair of dates determine what the
    number of delta days are and calculate the rate for that period.

    Args:
      next_date: A datetime.date object (the latter of two dates).
      current_date: A datetime.date object (the earlier of two dates).

    Returns:
      float; The fraction of a year times the rate.
    """
    delta = next_date - current_date
    # The rate for this period is the fraction of a year times the APR.
    rate = self.rate
    # Conditionally use randomization.
    if FLAGS.variance and self.stddev:
      rate = random.normalvariate(self.rate, self.stddev)
    rate_fraction = (delta.days / 365.25) * rate
    return rate_fraction

  def BuildAmortization(self):
    """Conditionally run by AddAccount in GeneralLedger."""
    if not self.start_date:
      logging.warn(
          '%s: Cannot build an amortization schedule without a start date',
          self.name)
      return
    if self.value > 0:
      logging.warn(
          '%s: Accounts with start date are expected to be loans and have a '
          'negative value.', self.name)
      return

    monthly_rate = self.rate / 12
    # Balance is negative, we want P&I in positive.
    balance = -self.value

    if self.loan_months:
      numerator = monthly_rate * balance * (
          1 + monthly_rate)**self.loan_months
      denominator = (1 + monthly_rate)**self.loan_months - 1
      self._payment = numerator / denominator
      start_epoch = int(self.start_date.strftime('%s'))
      # Duplicate self.timespec but with a start date. Building a non-monthly
      # scheduled could be done, but requires more thought.
      monthly = croniter.croniter(' '.join(self.timespec.exprs), start_epoch)
      for _ in xrange(self.loan_months):
        #build a schedule
        current = monthly.get_current(datetime.datetime).date()
        interest = monthly_rate * balance
        principle = self._payment - interest
        balance -= principle
        self._amortization[current] = (interest, principle)
        monthly.get_next()
    else:
      # No timeframe, assume interest only
      self._payment = balance * monthly_rate


class GeneralLedger(object):
  """A singleton bundle of holdings, useful for calculating worth.

  Attributes:
    accounts: dict; Keyed by account name, values are account objects.
    cashflows: dict; Keyed by datetime.date, value is a list of cashflow
               objects.
    preciations: dict; Keyed by datetime.date, value is a list of account
                 objects.
    worth: property; Sum of all holdings.
    inflation: float; Expected inflation.
    property_tax: float; Expected tax rate.
    start_date: datetime.date; When to start.
    end_date: datetime.date; When to end.
  """

  def __init__(self):
    self.accounts = {}
    self.cashflows = defaultdict(list)
    self.preciations = defaultdict(list)

  def AddAccount(self, account):
    """Adds an account to this general ledger object.

    Args:
      account: An account object.
    """
    if not isinstance(account, Account):
      raise TypeError('%s is not an Account.' % account)
    if account.start_date:
      account.BuildAmortization()
    self.accounts[account.name] = account
    # If the account has a timeframe to appreciate or depreciate, add it to the
    # schedule.
    if account.timespec:
      current = account.timespec.get_current(datetime.datetime).date()
      self.preciations[current].append(account)
#      logging.debug('Preciation: %s', self.preciations[current])

  def ValidateSweeps(self):
    """Check sweep_to and pull_from attributes of all accounts for consistency.

    Raises:
      InvalidSweepError: When sweep details of an account reference an account
                         which does not exist.
    """
    for account_name, account in self.accounts.iteritems():
      if account.sweep_in not in self.accounts:
        raise InvalidSweepError(
            '%s specified %s as a sweep_to but this account does not exist.' %
            account_name, account.sweep_to)
      if account.sweep_out not in self.accounts:
        raise InvalidSweepError(
            '%s specified %s as a pull_from but this account does not exist.' %
            account_name, account.pull_from)
        # TODO: check for circular sweeps.

  def Sweep(self):
    """Rebalances accounts based on sweep rules."""
    for account in self.accounts:
      if account.sweep_out:
        sweep = account.sweep_out
        if account.value > sweep.amount:
          transfer = account.value - sweep.amount
          account.value -= transfer
          self.accounts[sweep.sweep_account] += transfer
      if account.sweep_in:
        sweep = account.sweep_in
        if account.value < sweep.amount:
          transfer =  sweep.amount - account.value
          account.value += transfer
          self.accounts[sweep.sweep_account] -= transfer


  @property
  def worth(self):
    """Sum of all holdings."""
    return sum([x.value for x in self.accounts.values()])

  @property
  def debt(self):
    """Total debt."""
    return sum([x.value for x in self.accounts.values() if x.value < 0])

  @property
  def assets(self):
    """Total assets."""
    return sum([x.value for x in self.accounts.values() if x.value > 0])

  @staticmethod
  def SplitDate(iso_date_string):
    """Given an iso date string return a date object.

    Args:
      iso_date_string: str; A date string, such as '2014-01-01'.
    Returns:
      A datetime.date object, or None.
    """
    try:
      year, month, day = [int(x) for x in iso_date_string.split('-')]
    except ValueError:
      return
    return datetime.date(year, month, day)


  def LoadConfig(self):
    """Creates objects from config file.

    This method will open a file handle to FLAGS.config, read the contents, and
    instantiate a GeneralLedger object and all necessary accounts, sweeps, and
    cashflows.
    """
    # Create the proto GeneralLedger object.
    gl_proto = simulator_pb2.GeneralLedger()
    config = open(FLAGS.config).read()
    Merge(config, gl_proto)

    # Load globals.
    self.inflation = gl_proto.inflation
    self.property_tax = gl_proto.property_tax
    # TODO: This should be optional, set to now.
    self.start_date = self.SplitDate(gl_proto.start_date)
    self.end_date = self.SplitDate(gl_proto.end_date)

    # Load accounts.
    for ap in gl_proto.account:
      account = Account(ap.name, ap.value)
      if ap.rate:
        account.rate = ap.rate
      if ap.sweep_out.sweep_account and ap.sweep_out.amount:
        account.sweep_out = SweepRule(
            ap.sweep_out.sweep_account, ap.sweep_out.amount)
      if ap.sweep_in.sweep_account and ap.sweep_in.amount:
        account.sweep_in = SweepRule(
            ap.sweep_in.sweep_account, ap.sweep_in.amount)
      if ap.timespec:
        account.timespec = croniter.croniter(ap.timespec)
      if ap.loan_months:
        account.loan_months = ap.loan_months
      if ap.start_date:
        account.start_date = GeneralLedger.SplitDate(
            ap.start_date)
      self.AddAccount(account)

    # Load cashflows.
    for cf in gl_proto.cashflow:
      cashflow = Cashflow(
          cf.name, cf.account, cf.timespec, cf.amount, cf.start_date,
          cf.end_date, cf.category, cf.stddev)
      # Key cashflows on the first timespec match date.
      cron_date = cashflow.timespec.get_current(datetime.datetime).date()
      self.cashflows[cron_date].append(cashflow)

  def CreditAccount(self, account, amount):
    """Add funds to an account."""
    self.accounts[account].value += amount

  def DebitAccount(self, account, amount):
    """Remove funds from an account."""
    self.accounts[account].value -= amount


class SweepRule(object):
  """How to transfer when an account gets too empty or full.

  This object is tied to an account. We sweep from one account to another when
  the balance exceeds our maximum or fails to meet our minimum requirements.

  Attributes:
    sweep_account: str; The account name to sweep to or from.
    amount: float; high or low water mark.
  """
  def __init__(self, sweep_account, amount):
    self.sweep_account = sweep_account
    self.amount = amount


class Cashflow(object):
  """A category of recurring monetary movement.

  This could be payday, taxes, daycare, IRA contributions...
  It will have a cron timespec-like attribute defining when the movement will
  happen. A Cashflow object is intended to be added to an account object.
  """

  def __init__(self, name, account, timespec, amount, starting='',
               ending='', category='', stddev=0.0):
    """Initiator.

    Args:
      name: str; Human-friendly cashflow name, i.e. 'credit card'.
      account: str; Name of the account to flow into our out of.
      timespec: str; A cron-like timespec string, '*/5 * * * *'
      amount: float; Positive or negative integer, think deposit/withdrawl.
      starting: str; When the flow should start, iso format '2013-01-01'.
      ending: str; Optional ending date for a flow in iso format.
      # TODO: can I get rid of this and imply based on + or -?
      category: str; Either 'expense' or 'income'. This is used for calculating
                emergency fund or tacking on inflation.
      stddev: float; The standard deviation of this flow, 0.0 for fixed flows.
              Use this for variable bills and income.
    """
    self.name = name
    self.account = account
    self.timespec = croniter.croniter(timespec)
    self.amount = amount  # TODO(ryanshea): How might I make this dynamic?
    self.starting = GeneralLedger.SplitDate(starting)
    self.ending = GeneralLedger.SplitDate(ending)
    self.category = category
    self.stddev = stddev


if __name__ == '__main__':
  argv = FLAGS(sys.argv)
  logging.basicConfig(level=logging.DEBUG)

  # Create a GeneralLedger object and populate from FLAGS.config.
  general_ledger = GeneralLedger()
  general_ledger.LoadConfig()

  loop_date = copy.copy(general_ledger.start_date)
  one_day = datetime.timedelta(days=1)

  while loop_date < general_ledger.end_date:
    # One day at a time.
    loop_date = loop_date + one_day

    # Process cashflows for this date.
    if loop_date in general_ledger.cashflows:
      while general_ledger.cashflows[loop_date]:
        popped = general_ledger.cashflows[loop_date].pop()
        # Update the account value based on the account attribute of the
        # cashflow object popped.
        if popped.stddev and FLAGS.variance:
          general_ledger.accounts[popped.account].value += (
              random.normalvariate(popped.amount, popped.stddev))
        else:
          general_ledger.accounts[popped.account].value += popped.amount
        logging.debug('cashflow: %s %s %s', popped.name, loop_date,
                      general_ledger.worth)
        next_date = popped.timespec.get_next(datetime.datetime).date()
        # Get the next date for this cashflow and put it into the general ledger
        # cashflows dict.
        general_ledger.cashflows[next_date].append(popped)

    # Process preciations. Should this be a function which takes loop_date?
    if loop_date in general_ledger.preciations:
      # Loop through each account that needs preciation today.
      while general_ledger.preciations[loop_date]:
        popped = general_ledger.preciations[loop_date].pop()
        logging.debug('preciating: %s %s', popped.name, loop_date)
        popped.Preciate()
        current_date = popped.timespec.get_current(datetime.datetime).date()
        next_date = popped.timespec.get_next(datetime.datetime).date()
        if next_date == current_date:
          raise CronError(
              'Specified cron is not advancing to the next date for %s, '
              'cron definition, %s' % (popped.name, popped.timespec.expanded))
        general_ledger.preciations[next_date].append(popped)

    # Sweep accounts.
    for account in general_ledger.accounts.values():
      if account.sweep_out:
        amount = account.sweep_out.amount
        destination = general_ledger.accounts[account.sweep_out.sweep_account]
        if account.value > amount:
          move = account.value - amount
          logging.debug(
              '%s has %s, which is more than the max (%s) by %s. Sweeping.',
                        account.name, account.value, amount, move)
          account.value -= move
          destination.value += move
  print 'You are worth:', general_ledger.worth
  for account in general_ledger.accounts.values():
    print account.name, ":", account.value

  ######## Next #########
  # For some reason I only preciate the mortgage 173 times - wtf.
  # Fix amortization to be based on account timespec, not a forced monthly
  # ...test this:Preciate can check for schedule, and subtract principle based on that.
  # - How do a base a cashflow on another asset (taxes are based on asset value)

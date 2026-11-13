"""FIX tag numbers used by the NYSE Pillar FIX Gateway (spec v6.0)."""

# --- Standard header / trailer -------------------------------------------------
BeginString = 8
BodyLength = 9
MsgType = 35
MsgSeqNum = 34
PossDupFlag = 43
SenderCompID = 49
SenderSubID = 50
SendingTime = 52
TargetCompID = 56
TargetSubID = 57
PossResend = 97
OnBehalfOfCompID = 115
OnBehalfOfSubID = 116
OrigSendingTime = 122
DeliverToCompID = 128
CheckSum = 10

HEADER_TAGS = (
    BeginString,
    BodyLength,
    MsgType,
    MsgSeqNum,
    PossDupFlag,
    SenderCompID,
    SenderSubID,
    SendingTime,
    TargetCompID,
    TargetSubID,
    PossResend,
    OnBehalfOfCompID,
    OnBehalfOfSubID,
    OrigSendingTime,
    DeliverToCompID,
)

# --- Session layer -------------------------------------------------------------
BeginSeqNo = 7
EndSeqNo = 16
NewSeqNo = 36
RefSeqNum = 45
Text = 58
RawDataLength = 95
RawData = 96
EncryptMethod = 98
HeartBtInt = 108
TestReqID = 112
GapFillFlag = 123
ResetSeqNumFlag = 141
RefTagID = 371
RefMsgType = 372
SessionRejectReason = 373
Username = 553
Password = 554
NextExpectedMsgSeqNum = 789
SessionStatus = 1409

# --- Application layer ---------------------------------------------------------
Account = 1
ClOrdID = 11
CumQty = 14
ExecID = 17
ExecInst = 18
ExecRefID = 19
ExecTransType = 20
LastMkt = 30
LastPx = 31
LastQty = 32
OrderID = 37
OrderQty = 38
OrdStatus = 39
OrdType = 40
OrigClOrdID = 41
Price = 44
Side = 54
Symbol = 55
TimeInForce = 59
TransactTime = 60
SettlementType = 63
SymbolSfx = 65
ClientID = 109
MinQty = 110
MaxFloor = 111
LocateReqd = 114
ExpireTime = 126
ExecType = 150
LeavesQty = 151
EffectiveTime = 168
TradingSessionID = 336
NoTradingSessions = 386
CxlRejResponseTo = 434
OrderCapacity = 528
ParticipationRate = 849
LocateBroker = 5700
SelfTradeType = 7928
SpecialOrdType = 9202
RoutingInst = 9303
OffsetPrice = 9403
ExtendedExecInst = 9416
IntroducingBadgeID = 9448
BillTo = 9449
ParentFirmClOrdID = 9451
ParentFirmMPID = 9453
InterestType = 9478
DealID = 9483
DOrderAuctionPrice = 9565
LiquidityIndicator = 9730
AttributedQuote = 20001
ProactiveIfLocked = 20002
CancelInsteadOfReprice = 20003
WorkingPrice = 20004
FlowIndicator = 20005
WorkingAwayFromDisplay = 20006
UnsolicitedAckType = 20007
ParticipantType = 20008
NanosecondSendingTime = 20009
NanosecondTransactTime = 20010
RouteToBroker = 20011
BrokerOMSID = 20012
SubIDIndicator = 20013
AllocationFirmMPID = 20022
AllocationFirmIntroducingBadgeID = 20024
CATIMID = 20042
TargetStrategy = 20046
Eligible10b18 = 20047  # spec field name: "10b-18"
AuctionEligible = 20048
MinParticipationRate = 20049
MaxParticipationRate = 20050
WouldPrice = 20051
RoutingStrategy = 20052
ExecutedTradingSession = 20053
RefDealID = 30002
AllocationIndicator = 30003
OriginalDealID = 30006
RefExecTimestamp = 30016

#: Reverse lookup: tag number -> field name.
NAMES: dict[int, str] = {
    v: k for k, v in dict(globals()).items() if isinstance(v, int) and not k.startswith("_") and k[0].isupper()
}
